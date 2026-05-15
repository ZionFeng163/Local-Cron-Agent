"""
TaskManager — 统一任务管理中枢
职责：
  - 读操作：直接查 SQLite（毫秒级）
  - 写操作：先写 DB（前端秒回），再异步推送到沙盒
  - 同步：启动时 + 定期双向同步 DB ↔ 沙盒 crontab
"""
import logging
import shlex
import subprocess
import threading
import json
from typing import List, Optional, Dict, Any

from models import (
    Task, init_db, get_all_tasks, get_task, upsert_task,
    update_task_status, delete_task, gen_id, _now,
    insert_task_run, update_task_runtime, mark_task_auto_heal,
    get_recent_task_runs, insert_task_heal_record,
    get_task_heal_records, count_task_heal_records
)
from redis_state import redis_state
from script_policy import normalize_script_path, render_script_command, script_name_from_path

logger = logging.getLogger(__name__)


class TaskManager:
    def __init__(self, scheduler=None):
        """
        Args:
            scheduler: APScheduler 实例，用于管理内置心跳任务
        """
        self.scheduler = scheduler
        init_db()
        self.run_log_file = "/home/ubuntu/.lca/task_runs.log"
        self.run_log_state_file = "/home/ubuntu/.lca/task_runs.offset"
        logger.info("📦 TaskManager 初始化完成，数据库已就绪")

    # ==================== 读操作（毫秒级，纯 DB）====================

    def list_tasks(self, source: Optional[str] = None) -> List[Task]:
        return get_all_tasks(source)

    def get_task(self, task_id: str) -> Optional[Task]:
        return get_task(task_id)

    # ==================== 写操作（DB 优先 + 异步推送）====================

    def create_task(self, name: str, source: str, cron_expr: str, script_path: str,
                    description: str = "", status: str = "running") -> Task:
        """创建任务：先写 DB，再异步推送到执行环境"""
        normalized_script_path = script_path
        if source == "sandbox":
            normalized_script_path = normalize_script_path(script_path) or ""
            if not normalized_script_path:
                raise ValueError("sandbox task must use /home/ubuntu/.lca/scripts/*.sh")

        task = Task(
            id=gen_id(), name=name, source=source,
            cron_expr=cron_expr, script_path=normalized_script_path,
            status=status, description=description,
            created_at=_now(), updated_at=_now(), last_synced_at="",
            monitor_enabled=1, consecutive_failures=0,
            last_run_at="", last_success_at="", last_exit_code=None,
            last_auto_heal_at=""
        )
        upsert_task(task)
        logger.info(f"✅ 任务已创建入库: [{task.source}] {task.name}")

        # 异步推送到实际执行环境
        if source == "sandbox":
            threading.Thread(target=self._sync_push_sandbox_add, args=(task,), daemon=True).start()
        elif source == "internal" and self.scheduler:
            self._apply_internal_task(task)

        return task

    def toggle_task(self, task_id: str) -> Optional[Task]:
        """切换任务状态：先更新 DB，再异步推送"""
        with redis_state.lock(f"task:{task_id}", ttl=20):
            task = get_task(task_id)
            if not task:
                return None

            new_status = "paused" if task.status == "running" else "running"
            update_task_status(task_id, new_status)
            task.status = new_status
            logger.info(f"🔄 任务状态已更新: {task.name} → {new_status}")

            if task.source == "sandbox":
                threading.Thread(target=self._sync_push_sandbox_toggle, args=(task,), daemon=True).start()
            elif task.source == "internal" and self.scheduler:
                if new_status == "paused":
                    self.scheduler.pause_job(task.id)
                else:
                    self.scheduler.resume_job(task.id)

            return task

    def remove_task(self, task_id: str) -> bool:
        """删除任务：先从 DB 删除，再异步清除执行环境"""
        with redis_state.lock(f"task:{task_id}", ttl=20):
            task = get_task(task_id)
            if not task:
                return False

            delete_task(task_id)
            logger.info(f"🗑️ 任务已从 DB 删除: {task.name}")

            if task.source == "sandbox":
                threading.Thread(target=self._sync_push_sandbox_full, daemon=True).start()

            return True

    # ==================== 内部心跳同步 ====================

    # 基础设施任务 ID，不应暴露给用户
    _INFRA_JOB_IDS = {"db_sync_job", "task_monitor_job"}

    def sync_internal_tasks(self):
        """将 APScheduler 中的用户级内置任务同步到 DB"""
        if not self.scheduler:
            return
        for job in self.scheduler.get_jobs():
            if job.id in self._INFRA_JOB_IDS:
                continue  # 跳过基础设施任务
            existing = get_task(job.id)
            status = "running" if job.next_run_time else "paused"
            task = Task(
                id=job.id,
                name="内置系统体检心跳",
                source="internal",
                cron_expr="interval 3600s",
                script_path="",
                status=status,
                description="每小时自动巡检沙盒健康状态",
                created_at=existing.created_at if existing else _now(),
                updated_at=_now(),
                last_synced_at=_now(),
                monitor_enabled=0,
                consecutive_failures=existing.consecutive_failures if existing else 0,
                last_run_at=existing.last_run_at if existing else "",
                last_success_at=existing.last_success_at if existing else "",
                last_exit_code=existing.last_exit_code if existing else None,
                last_auto_heal_at=existing.last_auto_heal_at if existing else "",
            )
            upsert_task(task)
        logger.info("🔄 内置任务已同步至 DB")

    def _apply_internal_task(self, task: Task):
        """将 DB 中的内置任务状态应用到 APScheduler"""
        if task.status == "paused":
            try:
                self.scheduler.pause_job(task.id)
            except Exception:
                pass
        else:
            try:
                self.scheduler.resume_job(task.id)
            except Exception:
                pass

    # ==================== 沙盒同步（后台线程，不阻塞前端）====================

    def sync_sandbox_tasks(self):
        """从沙盒 crontab 拉取任务并同步到 DB"""
        attempts = 3
        out = ""
        while attempts > 0:
            try:
                safe_cmd = shlex.quote("crontab -l")
                result = subprocess.run(
                    f"/usr/local/bin/multipass exec agent-sandbox -- bash -c {safe_cmd}",
                    shell=True, capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0 or "no crontab for" in result.stderr.lower():
                    out = result.stdout.strip()
                    break
                else:
                    logger.warning(f"沙盒同步尝试失败: {result.stderr.strip()}, 剩余重试: {attempts-1}")
            except subprocess.TimeoutExpired:
                logger.warning(f"沙盒同步超时(60s), 剩余重试: {attempts-1}")
            except Exception as e:
                logger.error(f"沙盒同步异常: {e}")

            attempts -= 1
            if attempts > 0:
                import time
                time.sleep(2)

        if attempts == 0 and not out:
            logger.error("沙盒同步彻底失败，将沿用 DB 历史数据")
            return

        if not out or "no crontab" in out.lower():
            logger.info("沙盒 crontab 为空")
            return

        sandbox_tasks_from_cron = []
        lines = out.split("\n")
        for line in lines:
            line = line.strip()
            if not line or line.startswith("# ") or line.startswith("#!"):
                continue

            is_paused = line.startswith("#⏸️ ")
            display_line = line.replace("#⏸️ ", "") if is_paused else line
            parts = display_line.split(" ", 5)

            if len(parts) >= 6:
                cron_expr = " ".join(parts[:5])
                command_part = parts[5]
            else:
                cron_expr = "???"
                command_part = display_line

            parsed = self._extract_script_path_and_task_id(command_part)
            if not parsed.get("script_path"):
                logger.info("跳过非新版脚本型 crontab: %s", line)
                continue

            sandbox_tasks_from_cron.append({
                "cron_expr": cron_expr,
                "script_path": parsed["script_path"],
                "task_id_hint": parsed.get("task_id"),
                "status": "paused" if is_paused else "running",
                "raw": line
            })

        db_sandbox_tasks = get_all_tasks(source="sandbox")
        db_script_map = {t.script_path.strip(): t for t in db_sandbox_tasks}

        for ct in sandbox_tasks_from_cron:
            script_key = ct["script_path"].strip()
            matched = None

            if ct.get("task_id_hint"):
                matched = get_task(ct["task_id_hint"])
                if matched and matched.source != "sandbox":
                    matched = None

            if not matched and script_key in db_script_map:
                matched = db_script_map[script_key]

            if matched:
                matched.cron_expr = ct["cron_expr"]
                matched.status = ct["status"]
                matched.last_synced_at = _now()
                matched.updated_at = _now()
                upsert_task(matched)
                db_script_map.pop(matched.script_path.strip(), None)
            else:
                new_task_id = ct.get("task_id_hint") or gen_id()
                task = Task(
                    id=new_task_id,
                    name=script_name_from_path(script_key),
                    source="sandbox",
                    cron_expr=ct["cron_expr"],
                    script_path=script_key,
                    status=ct["status"],
                    description="从沙盒 crontab 自动导入",
                    created_at=_now(),
                    updated_at=_now(),
                    last_synced_at=_now(),
                    monitor_enabled=1,
                    consecutive_failures=0,
                    last_run_at="",
                    last_success_at="",
                    last_exit_code=None,
                    last_auto_heal_at="",
                )
                upsert_task(task)
                logger.info(f"📥 从沙盒导入新任务: {task.name}")

        for orphan in db_script_map.values():
            # 不再因为单次同步差异就直接删库，避免“任务一闪而过”
            # 典型场景：异步推送尚未完成、沙盒短暂不可达、crontab 读取瞬时失败
            logger.warning(f"⚠️ 沙盒未匹配到任务，先保留DB记录: {orphan.name} ({orphan.id})")

        logger.info(f"🔄 沙盒同步完成，共 {len(sandbox_tasks_from_cron)} 条任务")

    def _run_sandbox(self, cmd: str, timeout: int = 20, capture_output: bool = False) -> subprocess.CompletedProcess:
        safe_cmd = shlex.quote(cmd)
        return subprocess.run(
            f"/usr/local/bin/multipass exec agent-sandbox -- bash -c {safe_cmd}",
            shell=True,
            capture_output=capture_output,
            text=True,
            timeout=timeout
        )

    def _ensure_runlog_dir(self):
        self._run_sandbox("mkdir -p /home/ubuntu/.lca/scripts && touch /home/ubuntu/.lca/task_runs.log", timeout=10)

    def _build_wrapped_command(self, task: Task) -> str:
        return render_script_command(task.script_path)

    def _render_cron_entry(self, task: Task) -> str:
        command = self._build_wrapped_command(task)
        entry = f"{task.cron_expr} {command} #LCA_TASK_ID={task.id}"
        if task.status == "paused":
            return f"#⏸️ {entry}"
        return entry

    def _extract_script_path_and_task_id(self, command_part: str) -> Dict[str, Any]:
        task_id = None
        script_path = ""
        command = command_part.strip()
        marker = "#LCA_TASK_ID="
        if marker in command_part:
            before, after = command_part.split(marker, 1)
            command = before.strip()
            hint = after.strip().split(" ")[0]
            if hint:
                task_id = hint

        try:
            parts = shlex.split(command)
        except Exception:
            parts = []
        if len(parts) == 2 and parts[0] == "bash":
            script_path = normalize_script_path(parts[1]) or ""
        else:
            script_path = normalize_script_path(command) or ""

        return {"script_path": script_path, "task_id": task_id}


    def _sync_push_sandbox_add(self, task: Task):
        """将单条新任务推送到沙盒 crontab"""
        try:
            with redis_state.lock("sandbox:crontab", ttl=45, wait_timeout=10):
                self._ensure_runlog_dir()
                entry = self._render_cron_entry(task)
                safe_entry = shlex.quote(entry)
                sh_cmd = f"(crontab -l 2>/dev/null; echo {safe_entry}) | crontab -"
                safe_cmd = shlex.quote(sh_cmd)
                subprocess.run(
                    f"/usr/local/bin/multipass exec agent-sandbox -- bash -c {safe_cmd}",
                    shell=True, timeout=20
                )
                update_task_status(task.id, task.status)
                task.last_synced_at = _now()
                upsert_task(task)
            logger.info(f"📤 任务已推送至沙盒: {task.name}")
        except Exception as e:
            logger.error(f"推送到沙盒失败: {e}")

    def _sync_push_sandbox_toggle(self, task: Task):
        """切换沙盒中某条任务的暂停/恢复状态"""
        try:
            with redis_state.lock("sandbox:crontab", ttl=45, wait_timeout=10):
                safe_cmd = shlex.quote("crontab -l")
                result = subprocess.run(
                    f"/usr/local/bin/multipass exec agent-sandbox -- bash -c {safe_cmd}",
                    shell=True, capture_output=True, text=True, timeout=20
                )
                current = result.stdout

                new_lines = []
                target_marker = f"#LCA_TASK_ID={task.id}"
                for line in current.split("\n"):
                    stripped = line.strip()
                    if not stripped:
                        new_lines.append(line)
                        continue
                    if target_marker in stripped:
                        if task.status == "paused" and not stripped.startswith("#⏸️ "):
                            new_lines.append(f"#⏸️ {stripped}")
                        elif task.status == "running" and stripped.startswith("#⏸️ "):
                            new_lines.append(stripped.replace("#⏸️ ", "", 1))
                        else:
                            new_lines.append(line)
                    else:
                        new_lines.append(line)

                new_crontab = "\n".join(new_lines)
                safe_cron = shlex.quote(new_crontab)
                subprocess.run(
                    f"echo {safe_cron} | /usr/local/bin/multipass exec agent-sandbox -- crontab -",
                    shell=True, timeout=20
                )
            logger.info(f"📤 沙盒任务状态已同步: {task.name} → {task.status}")
        except Exception as e:
            logger.error(f"沙盒 toggle 同步失败: {e}")

    def _sync_push_sandbox_full(self):
        """用 DB 数据全量覆写沙盒 crontab"""
        try:
            with redis_state.lock("sandbox:crontab", ttl=45, wait_timeout=10):
                self._ensure_runlog_dir()
                sandbox_tasks = get_all_tasks(source="sandbox")
                lines = [self._render_cron_entry(t) for t in sandbox_tasks]

                new_crontab = "\n".join(lines) + "\n" if lines else ""
                safe_cron = shlex.quote(new_crontab)
                subprocess.run(
                    f"echo {safe_cron} | /usr/local/bin/multipass exec agent-sandbox -- crontab -",
                    shell=True, timeout=20
                )
            logger.info(f"📤 沙盒 crontab 已全量重写（{len(lines)} 条）")
        except Exception as e:
            logger.error(f"沙盒全量同步失败: {e}")

    # ==================== 任务运行监测与自愈 ====================

    def collect_task_run_events(self) -> List[Dict[str, Any]]:
        """从沙盒拉取增量运行日志事件"""
        try:
            self._ensure_runlog_dir()
            safe_script = shlex.quote(
                f"""
OFFSET=0
if [ -f {self.run_log_state_file} ]; then
  OFFSET=$(cat {self.run_log_state_file} 2>/dev/null || echo 0)
fi
TOTAL=$(wc -l < {self.run_log_file} 2>/dev/null || echo 0)
if [ "$OFFSET" -gt "$TOTAL" ]; then
  OFFSET=0
fi
if [ "$TOTAL" -gt "$OFFSET" ]; then
  sed -n "$((OFFSET+1)),$TOTAL p" {self.run_log_file}
fi
echo "$TOTAL" > {self.run_log_state_file}
"""
            )
            result = subprocess.run(
                f"/usr/local/bin/multipass exec agent-sandbox -- bash -c {safe_script}",
                shell=True, capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                logger.warning(f"任务运行日志拉取失败: {result.stderr.strip()}")
                return []

            events = []
            for line in (result.stdout or "").splitlines():
                text = line.strip()
                if not text:
                    continue
                try:
                    events.append(json.loads(text))
                except Exception:
                    logger.warning(f"跳过无法解析的运行日志: {text[:120]}")
            return events
        except Exception as e:
            logger.error(f"collect_task_run_events 异常: {e}")
            return []

    def persist_task_run_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        task_id = event.get("task_id")
        if not task_id:
            return None

        task = get_task(task_id)
        if not task:
            return None

        run_at = event.get("ts") or _now()
        exit_code = int(event.get("exit_code", 1))
        status = event.get("status") or ("success" if exit_code == 0 else "failed")
        duration_ms = int(event.get("duration_ms", 0)) if str(event.get("duration_ms", "")).isdigit() else 0
        output_tail = str(event.get("output_tail", ""))[:500]
        run_id = event.get("run_id") or f"{task_id}-{run_at}-{exit_code}-{abs(hash(output_tail)) % 100000}"

        inserted = insert_task_run(
            run_id=run_id,
            task_id=task_id,
            run_at=run_at,
            exit_code=exit_code,
            status=status,
            output_tail=output_tail,
            duration_ms=duration_ms,
            source="cron"
        )
        if not inserted:
            return None

        failures = update_task_runtime(task_id, run_at, exit_code)
        updated_task = get_task(task_id)
        return {
            "task": updated_task,
            "failures": failures,
            "exit_code": exit_code,
            "run_at": run_at,
            "status": status,
        }

    def probe_task_once(self, task_id: str, reason: str = "scheduled") -> Optional[Dict[str, Any]]:
        """简化监测：直接执行一次任务命令并记录结果"""
        task = get_task(task_id)
        if not task or task.source != "sandbox" or task.status != "running":
            return None

        try:
            result = self._run_sandbox(render_script_command(task.script_path), timeout=60, capture_output=True)
            exit_code = result.returncode
            now_ts = _now()
            output_tail = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()[:500]
            run_id = f"probe-{task_id}-{now_ts}-{abs(hash(output_tail)) % 100000}"
            insert_task_run(
                run_id=run_id,
                task_id=task_id,
                run_at=now_ts,
                exit_code=exit_code,
                status="success" if exit_code == 0 else "failed",
                output_tail=output_tail,
                duration_ms=0,
                source=f"probe:{reason}"
            )
            failures = update_task_runtime(task_id, now_ts, exit_code)
            updated_task = get_task(task_id)
            return {
                "task": updated_task,
                "failures": failures,
                "exit_code": exit_code,
                "run_at": now_ts,
                "status": "success" if exit_code == 0 else "failed",
            }
        except Exception as e:
            logger.error(f"probe_task_once 异常: {e}")
            return None

    def _classify_failure(self, exit_code: Optional[int], output_tail: str) -> str:
        text = (output_tail or "").lower()

        if exit_code == 127 or "command not found" in text or "no such file or directory" in text:
            return "command_missing"
        if "permission denied" in text:
            return "permission_error"
        if any(k in text for k in [
            "temporary failure in name resolution",
            "connection timed out",
            "connection reset",
            "network is unreachable",
            "failed to establish a new connection",
        ]):
            return "transient_network"
        return "unknown"

    def _extract_safe_exec_path(self, script_path: str) -> Optional[str]:
        return normalize_script_path(script_path)

    def _run_heal_action(self, task: Task, category: str) -> Dict[str, Any]:
        if category == "command_missing":
            return {
                "action": "pause_only",
                "ok": False,
                "needs_retry": False,
                "msg": "检测到命令不存在，跳过自动重试"
            }

        if category == "permission_error":
            target = self._extract_safe_exec_path(task.script_path)
            if not target:
                return {
                    "action": "skip_unsafe_chmod",
                    "ok": False,
                    "needs_retry": False,
                    "msg": "权限异常但命令不在白名单路径，跳过 chmod"
                }

            chmod_result = self._run_sandbox(f"chmod +x {shlex.quote(target)}", timeout=20, capture_output=True)
            if chmod_result.returncode != 0:
                return {
                    "action": "chmod_plus_retry",
                    "ok": False,
                    "needs_retry": False,
                    "msg": "尝试 chmod +x 失败",
                    "stderr": (chmod_result.stderr or "").strip()[:200],
                }

            return {
                "action": "chmod_plus_retry",
                "ok": True,
                "needs_retry": True,
                "msg": "已执行 chmod +x，准备重试"
            }

        if category == "transient_network":
            import time
            time.sleep(3)
            return {
                "action": "wait_and_retry",
                "ok": True,
                "needs_retry": True,
                "msg": "检测到网络瞬时异常，等待后重试"
            }

        return {
            "action": "direct_retry",
            "ok": True,
            "needs_retry": True,
            "msg": "未命中特征，直接重试一次"
        }

    def _record_heal_result(self, task_id: str, trigger: str, category: str, action: str,
                            ok: bool, message: str, exit_code: Optional[int] = None,
                            failures: Optional[int] = None):
        try:
            insert_task_heal_record(
                heal_id=f"hr-{gen_id()}",
                task_id=task_id,
                trigger=trigger,
                category=category,
                action=action,
                ok=ok,
                message=message,
                exit_code=exit_code,
                failures=failures,
                created_at=_now(),
            )
        except Exception as e:
            logger.warning(f"记录自愈历史失败: task_id={task_id} err={e}")

    def get_heal_catalog(self) -> Dict[str, Any]:
        return {
            "categories": [
                {"code": "manual_override", "label": "手动触发", "meaning": "用户手动点击立即修复"},
                {"code": "command_missing", "label": "命令缺失", "meaning": "命令不存在或文件路径不存在"},
                {"code": "permission_error", "label": "权限错误", "meaning": "执行权限不足"},
                {"code": "transient_network", "label": "网络瞬时故障", "meaning": "网络抖动、超时等可重试故障"},
                {"code": "unknown", "label": "未知", "meaning": "未命中已知分类规则"},
            ],
            "actions": [
                {"code": "direct_retry", "label": "直接重试", "meaning": "按原命令立即重试一次"},
                {"code": "pause_only", "label": "仅暂停", "meaning": "不再重试，直接进入暂停处理"},
                {"code": "chmod_plus_retry", "label": "修权限后重试", "meaning": "先 chmod +x 再重试"},
                {"code": "wait_and_retry", "label": "等待后重试", "meaning": "等待短时间后重试"},
                {"code": "skip_unsafe_chmod", "label": "拒绝危险修复", "meaning": "路径不在白名单，拒绝 chmod"},
            ]
        }

    def get_heal_records(self, limit: int = 50, offset: int = 0, task_id: str = "", trigger: str = "",
                         category: str = "", action: str = "", ok: Optional[int] = None) -> Dict[str, Any]:
        safe_limit = max(1, min(limit, 200))
        safe_offset = max(0, offset)
        items = get_task_heal_records(
            limit=safe_limit,
            offset=safe_offset,
            task_id=task_id,
            trigger=trigger,
            category=category,
            action=action,
            ok=ok,
        )
        total = count_task_heal_records(task_id=task_id, trigger=trigger, category=category, action=action, ok=ok)
        return {"items": items, "total": total, "limit": safe_limit, "offset": safe_offset}

    def try_heal_task(self, task_id: str, reason: str = "manual") -> Dict[str, Any]:

        task = get_task(task_id)
        if not task:
            return {"ok": False, "msg": "任务不存在"}
        if task.source != "sandbox":
            return {"ok": False, "msg": "仅支持沙盒任务自愈"}

        reason_text = reason or "manual"
        manual_request = reason_text.startswith("manual")
        if task.status != "running" and not manual_request:
            return {"ok": False, "msg": "任务已暂停，跳过自愈"}

        category = "manual_override" if manual_request else "unknown"
        action_info: Dict[str, Any] = {
            "action": "direct_retry",
            "ok": True,
            "needs_retry": True,
            "msg": "手动触发，直接重试一次"
        }

        if not manual_request:
            last_exit_code = task.last_exit_code
            last_output_tail = ""
            recent = self.get_task_runs(task_id, limit=1)
            if recent:
                last = recent[0]
                if last.get("status") == "failed":
                    last_exit_code = last.get("exit_code")
                    last_output_tail = last.get("output_tail") or ""

            try:
                parsed_exit = int(last_exit_code) if last_exit_code is not None else None
            except Exception:
                parsed_exit = None
            category = self._classify_failure(parsed_exit, last_output_tail)
            action_info = self._run_heal_action(task, category)

        action_name = action_info.get("action", "direct_retry")

        if not action_info.get("needs_retry", True):
            msg = action_info.get("msg", "自愈动作失败")
            if task.status == "running":
                updated = self.toggle_task(task_id)
                if updated and updated.status == "paused":
                    msg = f"{msg}，任务已自动暂停"

            if not manual_request:
                mark_task_auto_heal(task_id, _now())

            self._record_heal_result(
                task_id=task_id,
                trigger=reason_text,
                category=category,
                action=action_name,
                ok=False,
                message=msg,
                failures=getattr(task, "consecutive_failures", 0),
            )
            return {
                "ok": False,
                "category": category,
                "action": action_name,
                "msg": msg,
            }

        try:
            result = self._run_sandbox(render_script_command(task.script_path), timeout=60, capture_output=True)
            exit_code = result.returncode
            now_ts = _now()
            output_tail = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()[:500]
            run_id = f"heal-{task_id}-{now_ts}-{abs(hash(reason_text + action_name)) % 100000}"
            insert_task_run(
                run_id=run_id,
                task_id=task_id,
                run_at=now_ts,
                exit_code=exit_code,
                status="success" if exit_code == 0 else "failed",
                output_tail=output_tail,
                duration_ms=0,
                source=f"heal:{reason_text}:{action_name}"
            )
            failures = update_task_runtime(task_id, now_ts, exit_code)

            if not manual_request:
                mark_task_auto_heal(task_id, now_ts)

            if exit_code == 0:
                if task.status == "paused":
                    resumed = self.toggle_task(task_id)
                    if resumed and resumed.status == "running":
                        msg = "手动修复成功，任务已自动启动"
                    else:
                        msg = "手动修复成功，但自动启动失败，请手动点击启动"
                else:
                    msg = "自愈重试成功"

                self._record_heal_result(
                    task_id=task_id,
                    trigger=reason_text,
                    category=category,
                    action=action_name,
                    ok=True,
                    message=msg,
                    exit_code=exit_code,
                    failures=failures,
                )
                return {
                    "ok": True,
                    "category": category,
                    "action": action_name,
                    "msg": msg,
                    "exit_code": exit_code,
                    "failures": failures,
                }

            if task.status == "running":
                updated = self.toggle_task(task_id)
                if updated and updated.status == "paused":
                    msg = "自愈重试失败，任务已自动暂停"
                else:
                    msg = "自愈重试失败"
            else:
                msg = "手动修复失败（任务保持暂停）"

            self._record_heal_result(
                task_id=task_id,
                trigger=reason_text,
                category=category,
                action=action_name,
                ok=False,
                message=msg,
                exit_code=exit_code,
                failures=failures,
            )
            return {
                "ok": False,
                "category": category,
                "action": action_name,
                "msg": msg,
                "exit_code": exit_code,
                "failures": failures,
            }
        except Exception as e:
            msg = f"自愈执行异常: {e}"
            if not manual_request:
                mark_task_auto_heal(task_id, _now())
            self._record_heal_result(
                task_id=task_id,
                trigger=reason_text,
                category=category,
                action=action_name,
                ok=False,
                message=msg,
            )
            return {
                "ok": False,
                "category": category,
                "action": action_name,
                "msg": msg
            }


    def get_task_runs(self, task_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        return get_recent_task_runs(task_id, limit)
