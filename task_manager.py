"""
TaskManager — 统一任务管理中枢
职责：
  - 读操作：直接查 SQLite（毫秒级）
  - 写操作：先写 DB（前端秒回），再异步推送到沙盒
  - 同步：启动时 + 定期双向同步 DB ↔ 沙盒 crontab
"""
import asyncio
import logging
import shlex
import subprocess
import threading
from typing import List, Optional

from models import (
    Task, init_db, get_all_tasks, get_task, upsert_task,
    update_task_status, delete_task, delete_tasks_by_source, gen_id, _now
)

logger = logging.getLogger(__name__)


class TaskManager:
    def __init__(self, scheduler=None):
        """
        Args:
            scheduler: APScheduler 实例，用于管理内置心跳任务
        """
        self.scheduler = scheduler
        init_db()
        logger.info("📦 TaskManager 初始化完成，数据库已就绪")

    # ==================== 读操作（毫秒级，纯 DB）====================

    def list_tasks(self, source: Optional[str] = None) -> List[Task]:
        return get_all_tasks(source)

    def get_task(self, task_id: str) -> Optional[Task]:
        return get_task(task_id)

    # ==================== 写操作（DB 优先 + 异步推送）====================

    def create_task(self, name: str, source: str, cron_expr: str, command: str,
                    description: str = "", status: str = "running") -> Task:
        """创建任务：先写 DB，再异步推送到执行环境"""
        task = Task(
            id=gen_id(), name=name, source=source,
            cron_expr=cron_expr, command=command,
            status=status, description=description,
            created_at=_now(), updated_at=_now(), last_synced_at=""
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
    _INFRA_JOB_IDS = {"db_sync_job"}

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
                command="system_check_job",
                status=status,
                description="每小时自动巡检沙盒健康状态",
                created_at=existing.created_at if existing else _now(),
                updated_at=_now(),
                last_synced_at=_now()
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
                time.sleep(2)  # 等待一会再试
        
        if attempts == 0 and not out:
            logger.error("沙盒同步彻底失败，将沿用 DB 历史数据")
            return

        if not out or "no crontab" in out.lower():
            # 沙盒没任务，清理 DB 中 source=sandbox 的条目
            # 但不立即清除，保留用户手动创建但尚未推送的
            logger.info("沙盒 crontab 为空")
            return

        # 解析 crontab 行
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
                command = parts[5]
            else:
                cron_expr = "???"
                command = display_line

            sandbox_tasks_from_cron.append({
                "cron_expr": cron_expr,
                "command": command,
                "status": "paused" if is_paused else "running",
                "raw": line
            })

        # 获取 DB 中当前的沙盒任务
        db_sandbox_tasks = get_all_tasks(source="sandbox")
        db_cmd_map = {t.command.strip(): t for t in db_sandbox_tasks}

        # 同步：crontab 中有的但 DB 不知道的 → 导入
        for ct in sandbox_tasks_from_cron:
            cmd_key = ct["command"].strip()
            if cmd_key in db_cmd_map:
                # 已存在，更新状态
                existing = db_cmd_map[cmd_key]
                existing.cron_expr = ct["cron_expr"]
                existing.status = ct["status"]
                existing.last_synced_at = _now()
                existing.updated_at = _now()
                upsert_task(existing)
                del db_cmd_map[cmd_key]
            else:
                # 新任务，导入
                task = Task(
                    id=gen_id(),
                    name=cmd_key.split("/")[-1] if "/" in cmd_key else cmd_key[:30],
                    source="sandbox",
                    cron_expr=ct["cron_expr"],
                    command=ct["command"],
                    status=ct["status"],
                    description="从沙盒 crontab 自动导入",
                    created_at=_now(), updated_at=_now(), last_synced_at=_now()
                )
                upsert_task(task)
                logger.info(f"📥 从沙盒导入新任务: {task.name}")

        # DB 中有但 crontab 里没有的 → 标记为已被外部删除
        for orphan in db_cmd_map.values():
            delete_task(orphan.id)
            logger.info(f"🧹 清理已从沙盒消失的任务: {orphan.name}")

        logger.info(f"🔄 沙盒同步完成，共 {len(sandbox_tasks_from_cron)} 条任务")

    def _sync_push_sandbox_add(self, task: Task):
        """将单条新任务推送到沙盒 crontab"""
        try:
            entry = f"{task.cron_expr} {task.command}"
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
            safe_cmd = shlex.quote("crontab -l")
            result = subprocess.run(
                f"/usr/local/bin/multipass exec agent-sandbox -- bash -c {safe_cmd}",
                shell=True, capture_output=True, text=True, timeout=20
            )
            current = result.stdout

            # 查找并替换目标行
            new_lines = []
            for line in current.split("\n"):
                stripped = line.strip()
                # 匹配命令
                clean_line = stripped.replace("#⏸️ ", "")
                if task.command.strip() in clean_line:
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
            sandbox_tasks = get_all_tasks(source="sandbox")
            lines = []
            for t in sandbox_tasks:
                entry = f"{t.cron_expr} {t.command}"
                if t.status == "paused":
                    entry = f"#⏸️ {entry}"
                lines.append(entry)

            new_crontab = "\n".join(lines) + "\n" if lines else ""
            safe_cron = shlex.quote(new_crontab)
            subprocess.run(
                f"echo {safe_cron} | /usr/local/bin/multipass exec agent-sandbox -- crontab -",
                shell=True, timeout=20
            )
            logger.info(f"📤 沙盒 crontab 已全量重写（{len(lines)} 条）")
        except Exception as e:
            logger.error(f"沙盒全量同步失败: {e}")
