import asyncio
import logging
import os
import queue
import threading
from dotenv import load_dotenv

# 让本项目自身的 .env 文件优先级最高
load_dotenv(override=True)
import shlex
import subprocess
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from exquisite_agent.agents.react_fc import FCAgent
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

# 日志输出配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%H:%M:%S')

# 全局状态
scheduler = AsyncIOScheduler()
agent = None
orchestrator = None
task_mgr: TaskManager = None
connected_clients: List[WebSocket] = []


# ========== 核心后台心跳 ==========
def system_check_job():
    logging.info(">>> 触发定时心跳：执行系统体检与自动异常排查 <<<")
    if not agent:
        return
    task = "现在是一个小时一次的隐式后台巡检时间。请调用 Sandbox_Health_Scanner 检查状态。如果一切正常只回应一句话，不要使用多余工具凑步数。"
    try:
        response = agent.run(task)
        logging.info(f"✨ 调度体检汇报完毕:\n{response}")
    except Exception as e:
        logging.error(f"❌ 体检崩溃: {str(e)}")


# ========== WebSocket 消息群发机制 ==========
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
    logging.info("⏳ 后台异步调度器已激活。")

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

    # 实例化工具全家桶（传入 task_mgr）
    bash_tool = SandboxBashExecutor()
    cron_tool = SandboxCrontabAdmin(task_mgr=task_mgr)
    sys_tool = SandboxHealthScanner()
    svc_tool = SandboxServiceManager()
    sched_tool = AgentHeartbeatController(scheduler, task_mgr=task_mgr)
    file_writer_tool = SandboxFileWriter()
    search_tool = SearchTool()

    agent = FCAgent(
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


# ========== 统计 API ==========

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


# ========== 供前端聊天调用的 WS 全双工通道 ==========

@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        while True:
            user_msg = await websocket.receive_text()
            logging.info(f"UI 用户发送了: {user_msg}")

            q = queue.Queue()

            def sync_worker(msg, q_out):
                try:
                    # 使用 LangGraph 编排器运行流，并传入回调函数实时推送工具消息
                    for chunk in orchestrator.run_stream(msg, callback=lambda c: q_out.put(c)):
                        q_out.put(chunk)
                    q_out.put(None)
                except Exception as e:
                    q_out.put({"type": "message", "content": f"\n\n[核心异常]: {str(e)}"})
                    q_out.put(None)

            thread = threading.Thread(target=sync_worker, args=(user_msg, q))
            thread.start()

            await websocket.send_json({"type": "stream_start"})

            while True:
                item = await asyncio.to_thread(q.get)
                if item is None:
                    break
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
