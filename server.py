import asyncio
import logging
import os
import json
import queue
import threading
import uuid
from datetime import datetime
from dotenv import load_dotenv

# 让本项目自身的 .env 文件优先级最高
load_dotenv(override=True)
import shlex
import subprocess
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from exquisite_agent.llm import LLM

from tools.sandbox_bash_executor import SandboxBashExecutor
from tools.sandbox_crontab_admin import SandboxCrontabAdmin
from tools.sandbox_health_scanner import SandboxHealthScanner
from tools.sandbox_service_manager import SandboxServiceManager
from tools.agent_heartbeat_controller import AgentHeartbeatController
from tools.sandbox_file_writer import SandboxFileWriter
from exquisite_agent.tools.searchtool import SearchTool

from task_manager import TaskManager
from langgraph_orchestrator import LangGraphOrchestrator
from streaming_fc_agent import StreamingFCAgent
from redis_state import redis_state

# 日志输出配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%H:%M:%S')

# 全局状态
scheduler = AsyncIOScheduler()
agent = None
orchestrator = None
task_mgr: TaskManager = None
connected_clients: List[WebSocket] = []


# ========== 核心后台心跳 + 系统健康自愈 ==========
health_lock = threading.Lock()
last_system_health_report: Optional[Dict[str, Any]] = None


def _run_in_sandbox(cmd: str, timeout: int = 20) -> subprocess.CompletedProcess:
    safe_cmd = shlex.quote(cmd)
    return subprocess.run(
        f"/usr/local/bin/multipass exec agent-sandbox -- bash -c {safe_cmd}",
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _collect_system_health_raw() -> Dict[str, Any]:
    probe_script = """
python3 - <<'PY'
import json
import os
import shutil
import subprocess


def pct(path):
    total, used, _free = shutil.disk_usage(path)
    return round((used / total) * 100, 2) if total else 0.0

meminfo = {}
with open('/proc/meminfo', 'r') as f:
    for line in f:
        key, val = line.split(':', 1)
        meminfo[key] = int(val.strip().split()[0])

mem_total = meminfo.get('MemTotal', 0)
mem_available = meminfo.get('MemAvailable', 0)
mem_used_pct = round((1 - (mem_available / mem_total)) * 100, 2) if mem_total else 0.0

load1, load5, load15 = os.getloadavg()
cpu_count = os.cpu_count() or 1
cron_active = subprocess.run(
    'systemctl is-active cron', shell=True, capture_output=True, text=True
).stdout.strip()

top_process = subprocess.run(
    "ps -eo comm,%cpu --sort=-%cpu | sed -n '2p'",
    shell=True,
    capture_output=True,
    text=True,
).stdout.strip()

top_process_name = ""
top_process_cpu_percent = None
if top_process:
    parts = top_process.split()
    if len(parts) >= 2:
        top_process_name = parts[0]
        try:
            top_process_cpu_percent = float(parts[-1])
        except Exception:
            top_process_cpu_percent = None
    else:
        top_process_name = top_process

print(json.dumps({
    'disk_root_percent': pct('/'),
    'disk_home_percent': pct('/home/ubuntu'),
    'memory_used_percent': mem_used_pct,
    'load_1': round(load1, 2),
    'load_5': round(load5, 2),
    'load_15': round(load15, 2),
    'cpu_count': cpu_count,
    'cron_active': cron_active,
    'top_process': top_process,
    'top_process_name': top_process_name,
    'top_process_cpu_percent': top_process_cpu_percent,
}, ensure_ascii=False))
PY
"""
    result = _run_in_sandbox(probe_script, timeout=25)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "系统探针执行失败")
    out = (result.stdout or "").strip().split("\n")[-1]
    return json.loads(out)


def _metric_payload(label: str, value: Any, threshold: str, is_normal: bool, detail: str) -> Dict[str, Any]:
    return {
        "label": label,
        "value": value,
        "threshold": threshold,
        "is_normal": is_normal,
        "detail": detail,
    }


def _evaluate_health(raw: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    load_threshold = round(max(1.0, raw["cpu_count"] * 1.5), 2)

    metrics = {
        "disk_root": _metric_payload(
            "根分区使用率",
            f"{raw['disk_root_percent']}%",
            "<= 85%",
            raw["disk_root_percent"] <= 85,
            "根分区空间压力",
        ),
        "disk_home": _metric_payload(
            "Home 分区使用率",
            f"{raw['disk_home_percent']}%",
            "<= 85%",
            raw["disk_home_percent"] <= 85,
            "用户目录空间压力",
        ),
        "memory": _metric_payload(
            "内存使用率",
            f"{raw['memory_used_percent']}%",
            "<= 90%",
            raw["memory_used_percent"] <= 90,
            "系统内存压力",
        ),
        "load": _metric_payload(
            "系统负载(1m)",
            raw["load_1"],
            f"<= {load_threshold}",
            raw["load_1"] <= load_threshold,
            "短时负载是否过高",
        ),
        "cron_service": _metric_payload(
            "Cron 服务状态",
            raw["cron_active"] or "unknown",
            "active",
            (raw["cron_active"] == "active"),
            "定时任务底座服务",
        ),
    }
    return metrics


def _attempt_self_heal(raw: Dict[str, Any], metrics: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []

    if not metrics["cron_service"]["is_normal"]:
        result = _run_in_sandbox("sudo systemctl restart cron && systemctl is-active cron", timeout=20)
        actions.append({
            "action": "重启 cron 服务",
            "success": (result.returncode == 0 and "active" in (result.stdout or "")),
            "stdout": (result.stdout or "").strip(),
            "stderr": (result.stderr or "").strip(),
        })

    if raw["disk_root_percent"] > 85 or raw["disk_home_percent"] > 85:
        cleanup_cmd = "sudo find /tmp /var/tmp -xdev -type f -mtime +3 -delete"
        result = _run_in_sandbox(cleanup_cmd, timeout=30)
        actions.append({
            "action": "清理 /tmp 和 /var/tmp 的历史临时文件",
            "success": result.returncode == 0,
            "stdout": (result.stdout or "").strip(),
            "stderr": (result.stderr or "").strip(),
        })

    return actions


def run_system_health_check(trigger: str, auto_heal: bool = True) -> Dict[str, Any]:
    global last_system_health_report

    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report: Dict[str, Any] = {
        "checked_at": checked_at,
        "trigger": trigger,
        "auto_heal_enabled": auto_heal,
        "overall_is_normal": False,
        "metrics": {},
        "top_process": "",
        "self_heal": {
            "attempted": False,
            "actions": [],
            "summary": "未触发",
        },
        "error": None,
    }

    try:
        raw_before = _collect_system_health_raw()
        metrics_before = _evaluate_health(raw_before)
        before_ok = all(m["is_normal"] for m in metrics_before.values())

        report["metrics"] = metrics_before
        report["top_process"] = raw_before.get("top_process", "")
        report["top_process_name"] = raw_before.get("top_process_name", "")
        report["top_process_cpu_percent"] = raw_before.get("top_process_cpu_percent")
        report["overall_is_normal"] = before_ok

        if auto_heal and not before_ok:
            actions = _attempt_self_heal(raw_before, metrics_before)
            if actions:
                report["self_heal"]["attempted"] = True
                report["self_heal"]["actions"] = actions
                raw_after = _collect_system_health_raw()
                metrics_after = _evaluate_health(raw_after)
                after_ok = all(m["is_normal"] for m in metrics_after.values())
                report["metrics_before_heal"] = metrics_before
                report["metrics"] = metrics_after
                report["top_process"] = raw_after.get("top_process", "")
                report["top_process_name"] = raw_after.get("top_process_name", "")
                report["top_process_cpu_percent"] = raw_after.get("top_process_cpu_percent")
                report["overall_is_normal_before_heal"] = before_ok
                report["overall_is_normal"] = after_ok
                report["self_heal"]["summary"] = "修复后已恢复正常" if after_ok else "已尝试修复，但仍有异常"
            else:
                report["self_heal"]["summary"] = "检测到异常，但当前规则无可执行修复动作"

    except Exception as e:
        report["error"] = str(e)
        report["self_heal"]["summary"] = "检查流程异常"

    with health_lock:
        last_system_health_report = report

    return report




def system_check_job():
    logging.info(">>> 触发定时心跳：执行系统体检与自动异常排查 <<<")
    report = run_system_health_check(trigger="scheduled", auto_heal=True)
    if report.get("error"):
        logging.error(f"❌ 定时体检异常: {report['error']}")
    else:
        status = "正常" if report.get("overall_is_normal") else "异常"
        heal = report.get("self_heal", {}).get("summary", "未触发")
        logging.info(f"✨ 定时体检完成: 状态={status} | 自愈={heal}")


def task_monitor_job():
    """简化任务监测：每分钟主动探测一次运行中的沙盒任务"""
    if not task_mgr:
        return
    try:
        sandbox_tasks = task_mgr.list_tasks(source="sandbox")
        for task in sandbox_tasks:
            if task.status != "running":
                continue
            if not getattr(task, "monitor_enabled", 1):
                continue

            probe = task_mgr.probe_task_once(task.id, reason="scheduled")
            if not probe:
                continue

            failures = probe["failures"]
            if failures >= 3:
                result = task_mgr.try_heal_task(task.id, reason="auto_threshold")
                logging.warning(
                    f"🛠️ 任务自愈触发: {task.name} failures={failures} result={result.get('msg')}"
                )
    except Exception as e:
        logging.error(f"❌ 任务监测异常: {e}")

async def broadcast_refresh_jobs():
    dead_clients = []
    for client in connected_clients:
        try:
            await client.send_json({"type": "refresh_jobs"})
        except:
            dead_clients.append(client)
    for c in dead_clients:
        connected_clients.remove(c)


# ========== API 与框架生命周期 ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent, orchestrator, task_mgr
    logging.info("🚀 正在启动 Local-Cron-Agent 同步式 Web 后端 (Uvicorn)...")

    # 每 3600 秒自动运行内部体检
    scheduler.add_job(system_check_job, 'interval', seconds=3600, id="system_check_job")
    scheduler.start()

    # 初始化 TaskManager 并完成首次同步
    task_mgr = TaskManager(scheduler=scheduler)
    task_mgr.sync_internal_tasks()  # 内置任务 → DB（毫秒级）

    # 后台线程异步同步沙盒任务（不阻塞启动）
    def _bg_sync():
        task_mgr.sync_sandbox_tasks()
        logging.info("🎯 首次沙盒同步完成")
    threading.Thread(target=_bg_sync, daemon=True).start()

    # 注册定期同步（每 60 秒）
    def _periodic_sync():
        task_mgr.sync_internal_tasks()
        task_mgr.sync_sandbox_tasks()
    scheduler.add_job(_periodic_sync, 'interval', seconds=60, id="db_sync_job")

    # 注册任务监测（每 60 秒）
    scheduler.add_job(task_monitor_job, 'interval', seconds=60, id="task_monitor_job")

    logging.info("⏳ 后台异步调度器已激活。")

    # 启动后先做一次健康检查，避免前端首次进入无数据
    def _initial_health_probe():
        run_system_health_check(trigger="startup", auto_heal=True)
        logging.info("🩺 启动健康探针完成")
    threading.Thread(target=_initial_health_probe, daemon=True).start()

    # 实例化工具全家桶（传入 task_mgr）
    bash_tool = SandboxBashExecutor()
    cron_tool = SandboxCrontabAdmin(task_mgr=task_mgr)
    sys_tool = SandboxHealthScanner()
    svc_tool = SandboxServiceManager()
    sched_tool = AgentHeartbeatController(scheduler, task_mgr=task_mgr)
    file_writer_tool = SandboxFileWriter()
    search_tool = SearchTool()

    agent = StreamingFCAgent(
        llm=LLM(),
        name="Local-Cron-Agent",
        tools=[bash_tool, cron_tool, sys_tool, svc_tool, sched_tool, file_writer_tool, search_tool]
    )
    agent.max_iterations = 8
    
    # 初始化 LangGraph 编排器
    orchestrator = LangGraphOrchestrator(
        tools=[bash_tool, cron_tool, sys_tool, svc_tool, sched_tool, file_writer_tool, search_tool],
        task_mgr=task_mgr
    )

    agent.system_prompt = """
    你是 Local-Cron-Agent (自愈管家)。你可以和用户在 Web 界面对话。
    如果用户明确要求操作"底座/沙盒"定时任务，使用 Sandbox_Crontab_Admin。
    如果用户明确要求操作"内置心跳/自检"，使用 Agent_Heartbeat_Controller。
    如果遭遇"编写/下发/挂载长期脚本跑后台"的任务，一定先用 Sandbox_File_Writer 生成脚本源码进入沙盒环境，随后用 Sandbox_Crontab_Admin 将其装载在 Cron 中运行。
    当你缺乏外部情报时，可以调用 Search 进行谷歌搜索。
    
    【核心守则】：
    1. 当用户毫无特指地说"打印目前所有任务"、"列出任务"时，默认包含内置和沙盒任务，必须连续调用 list 功能。
    2. 【重点】创建任务时：
       - `task_name` 必须是精炼、人类可读的（如“备份脚本”），严禁填入 shell 代码。
       - `description` 必须详细说明该任务的目标（如“每隔5分钟检查一次cpu占用”）。
    
    执行完成后用友好的回答作结。
    """

    yield

    scheduler.shutdown()
    logging.info("🛑 正在优雅关闭 Web 后端。")

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== 任务 API（全部读写 DB，毫秒级响应）==========

def _task_to_dict(t):
    """将 Task 对象转为前端可用的 dict"""
    return {
        "id": t.id,
        "name": t.name,
        "source": t.source,
        "cron_expr": t.cron_expr,
        "command": t.command,
        "status": t.status.upper(),  # 前端用大写 RUNNING/PAUSED
        "description": t.description,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "monitor_enabled": bool(getattr(t, "monitor_enabled", 1)),
        "consecutive_failures": getattr(t, "consecutive_failures", 0),
        "last_run_at": getattr(t, "last_run_at", ""),
        "last_success_at": getattr(t, "last_success_at", ""),
        "last_exit_code": getattr(t, "last_exit_code", None),
        "last_auto_heal_at": getattr(t, "last_auto_heal_at", ""),
    }

@app.get("/api/jobs")
async def get_jobs():
    """统一返回所有任务（毫秒级，纯 DB 读取）"""
    all_tasks = task_mgr.list_tasks()
    internal = [_task_to_dict(t) for t in all_tasks if t.source == "internal"]
    ubuntu = [_task_to_dict(t) for t in all_tasks if t.source == "sandbox"]
    return {"internal_jobs": internal, "ubuntu_jobs": ubuntu}

@app.get("/api/jobs/internal")
async def get_internal_jobs():
    """⚡ 内置任务（毫秒级）"""
    tasks = task_mgr.list_tasks(source="internal")
    return {"internal_jobs": [_task_to_dict(t) for t in tasks]}

@app.get("/api/jobs/ubuntu")
async def get_ubuntu_jobs():
    """⚡ 沙盒任务（毫秒级，从 DB 读取）"""
    tasks = task_mgr.list_tasks(source="sandbox")
    return {"ubuntu_jobs": [_task_to_dict(t) for t in tasks]}


class ToggleReq(BaseModel):
    task_id: str = ""
    # 兼容旧接口
    job_id: str = ""
    raw_line: str = ""
    action: str = ""

@app.post("/api/jobs/toggle")
async def toggle_task(req: ToggleReq):
    """统一的 toggle 接口"""
    tid = req.task_id or req.job_id
    task = task_mgr.toggle_task(tid)
    if not task:
        return {"status": "error", "msg": "任务不存在"}
    await broadcast_refresh_jobs()
    return {"status": "success", "task": _task_to_dict(task)}

# 兼容旧的 toggle_internal / toggle_ubuntu 接口
@app.post("/api/jobs/toggle_internal")
async def toggle_internal(req: ToggleReq):
    tid = req.task_id or req.job_id
    task = task_mgr.toggle_task(tid)
    if not task:
        return {"status": "error", "msg": "任务不存在"}
    await broadcast_refresh_jobs()
    return {"status": "success"}

@app.post("/api/jobs/toggle_ubuntu")
async def toggle_ubuntu(req: ToggleReq):
    """兼容旧接口：通过 raw_line 查找任务 ID"""
    if req.task_id:
        task = task_mgr.toggle_task(req.task_id)
    else:
        # 兼容：按 raw_line 里的命令来查找
        all_sandbox = task_mgr.list_tasks(source="sandbox")
        target = None
        for t in all_sandbox:
            if t.command.strip() in (req.raw_line or ""):
                target = t
                break
        if target:
            task = task_mgr.toggle_task(target.id)
        else:
            return {"status": "error", "msg": "找不到匹配的沙盒任务"}
    await broadcast_refresh_jobs()
    return {"status": "success"}


class DeleteReq(BaseModel):
    task_id: str = ""
    raw_line: str = ""


class TaskHealReq(BaseModel):
    reason: str = "manual"

@app.post("/api/jobs/delete")
async def delete_task_api(req: DeleteReq):
    """统一的删除接口"""
    task_mgr.remove_task(req.task_id)
    await broadcast_refresh_jobs()
    return {"status": "success"}

@app.post("/api/jobs/delete_ubuntu")
async def delete_ubuntu(req: DeleteReq):
    """兼容旧接口"""
    if req.task_id:
        task_mgr.remove_task(req.task_id)
    else:
        all_sandbox = task_mgr.list_tasks(source="sandbox")
        for t in all_sandbox:
            if t.command.strip() in (req.raw_line or ""):
                task_mgr.remove_task(t.id)
                break
    await broadcast_refresh_jobs()
    return {"status": "success"}


@app.get("/api/tasks/health")
async def get_tasks_health():
    tasks = task_mgr.list_tasks(source="sandbox")
    return {
        "tasks": [_task_to_dict(t) for t in tasks],
        "summary": {
            "total": len(tasks),
            "failing": sum(1 for t in tasks if getattr(t, "consecutive_failures", 0) > 0),
            "auto_healed": sum(1 for t in tasks if getattr(t, "last_auto_heal_at", "")),
        }
    }


@app.get("/api/tasks/{task_id}/runs")
async def get_task_runs_api(task_id: str, limit: int = 20):
    safe_limit = max(1, min(limit, 200))
    runs = task_mgr.get_task_runs(task_id, safe_limit)
    return {"task_id": task_id, "runs": runs}


@app.post("/api/tasks/{task_id}/heal")
async def heal_task_api(task_id: str, req: TaskHealReq):
    result = task_mgr.try_heal_task(task_id, reason=req.reason or "manual")
    task = task_mgr.get_task(task_id)
    await broadcast_refresh_jobs()
    return {
        "status": "success" if result.get("ok") else "error",
        "result": result,
        "task": _task_to_dict(task) if task else None,
    }


@app.get("/api/heals/catalog")
async def get_heal_catalog_api():
    return task_mgr.get_heal_catalog()


@app.get("/api/heals/history")
async def get_heal_history_api(
    limit: int = 50,
    offset: int = 0,
    task_id: str = "",
    trigger: str = "",
    category: str = "",
    action: str = "",
    ok: Optional[int] = None,
):
    if ok is not None and ok not in (0, 1):
        raise HTTPException(status_code=400, detail="ok 参数仅支持 0 或 1")
    return task_mgr.get_heal_records(
        limit=limit,
        offset=offset,
        task_id=task_id,
        trigger=trigger,
        category=category,
        action=action,
        ok=ok,
    )


@app.get("/api/stats")
async def get_stats():
    """仪表盘统计（毫秒级）"""
    all_tasks = task_mgr.list_tasks()
    total = len(all_tasks)
    running = sum(1 for t in all_tasks if t.status == "running")
    paused = total - running
    internal = sum(1 for t in all_tasks if t.source == "internal")
    ubuntu = sum(1 for t in all_tasks if t.source == "sandbox")
    return {
        "total": total, "running": running, "paused": paused,
        "internal_count": internal, "ubuntu_count": ubuntu
    }


# ========== 沙盒文件浏览与编辑 API ==========

class SandboxFileReq(BaseModel):
    path: str
    content: str = ""

@app.get("/api/sandbox/ls")
async def sandbox_ls(path: str = "/home/ubuntu"):
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            f"/usr/local/bin/multipass exec agent-sandbox -- ls -la {shlex.quote(path)}",
            shell=True, capture_output=True, text=True, timeout=15
        )
        lines = result.stdout.strip().split("\n")
        items = []
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 9:
                name = " ".join(parts[8:])
                if name in [".", ".."]:
                    continue
                is_dir = parts[0].startswith("d")
                items.append({
                    "name": name, "is_dir": is_dir, "size": parts[4],
                    "modified": f"{parts[5]} {parts[6]} {parts[7]}",
                    "path": f"{path.rstrip('/')}/{name}"
                })
        return {"path": path, "items": items}
    except Exception as e:
        return {"path": path, "items": [], "error": str(e)}

@app.get("/api/sandbox/read")
async def sandbox_read(path: str):
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            f"/usr/local/bin/multipass exec agent-sandbox -- cat {shlex.quote(path)}",
            shell=True, capture_output=True, text=True, timeout=15
        )
        return {"path": path, "content": result.stdout, "error": result.stderr if result.returncode != 0 else None}
    except Exception as e:
        return {"path": path, "content": "", "error": str(e)}

@app.post("/api/sandbox/write")
async def sandbox_write(req: SandboxFileReq):
    import base64
    encoded = base64.b64encode(req.content.encode('utf-8')).decode('utf-8')
    cmd = f"echo '{encoded}' | base64 -d > {shlex.quote(req.path)}"
    safe_cmd = shlex.quote(cmd)
    try:
        await asyncio.to_thread(
            subprocess.run,
            f"/usr/local/bin/multipass exec agent-sandbox -- bash -c {safe_cmd}",
            shell=True, timeout=15
        )
        return {"status": "success", "path": req.path}
    except Exception as e:
        return {"status": "error", "msg": str(e)}




# ========== 日志 API ==========

@app.get("/api/logs")
async def get_logs(lines: int = 100):
    log_path = os.path.join(os.path.dirname(__file__), "backend.log")
    try:
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                all_lines = f.readlines()
                return {"lines": all_lines[-lines:]}
        return {"lines": []}
    except Exception as e:
        return {"lines": [], "error": str(e)}


class HealthCheckReq(BaseModel):
    auto_heal: bool = True


@app.get("/api/system/health")
async def get_system_health():
    with health_lock:
        report = last_system_health_report
    if report is None:
        report = run_system_health_check(trigger="api_fallback", auto_heal=True)
    return report


@app.post("/api/system/health/check")
async def trigger_system_health_check(req: HealthCheckReq):
    report = await asyncio.to_thread(run_system_health_check, "manual", req.auto_heal)
    return {"status": "success", "report": report}


@app.get("/api/agent/runs/{run_id}")
async def get_agent_run_status(run_id: str):
    status = redis_state.get_run_status(run_id)
    return {
        "run_id": run_id,
        "redis_enabled": redis_state.enabled,
        "status": status,
    }

@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        while True:
            user_msg = await websocket.receive_text()
            logging.info(f"UI 用户发送了: {user_msg}")

            q = queue.Queue()
            run_id = f"run-{uuid.uuid4().hex[:12]}"

            def sync_worker(msg, q_out, rid):
                try:
                    # 使用 LangGraph 编排器运行流，并传入回调函数实时推送工具消息
                    for chunk in orchestrator.run_stream(msg, callback=lambda c: q_out.put(c), run_id=rid):
                        q_out.put(chunk)
                    q_out.put(None)
                except Exception as e:
                    q_out.put({"type": "message", "content": f"\n\n[核心异常]: {str(e)}"})
                    q_out.put(None)

            thread = threading.Thread(target=sync_worker, args=(user_msg, q, run_id))
            thread.start()

            await websocket.send_json({"type": "stream_start", "run_id": run_id})
            last_heartbeat_ts = asyncio.get_running_loop().time()

            while True:
                try:
                    item = await asyncio.to_thread(q.get, True, 1.0)
                except queue.Empty:
                    now = asyncio.get_running_loop().time()
                    if now - last_heartbeat_ts >= 3:
                        await websocket.send_json({"type": "heartbeat", "content": "⏳ 正在处理中，请稍候..."})
                        last_heartbeat_ts = now
                    continue
                if item is None:
                    break
                last_heartbeat_ts = asyncio.get_running_loop().time()
                if item.get("type") == "tool_ended":
                    # 工具执行完毕后同步 DB 并通知前端
                    task_mgr.sync_internal_tasks()
                    await broadcast_refresh_jobs()
                    continue
                await websocket.send_json(item)

            await websocket.send_json({"type": "stream_end"})
            await broadcast_refresh_jobs()

    except WebSocketDisconnect:
        connected_clients.remove(websocket)
