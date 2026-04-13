import json
from exquisite_agent.tools.base import BaseTool


class AgentHeartbeatController(BaseTool):
    name = "Agent_Heartbeat_Controller"
    description = "控制本系统(Agent自身后台)内置的健康体检定时任务。可用于暂停、恢复自我巡检心跳。"

    def __init__(self, scheduler, task_mgr=None):
        self.scheduler = scheduler
        self.task_mgr = task_mgr

    def execute(self, action_input: str) -> str:
        try:
            args = json.loads(action_input)
            action = args.get("action")
            job_id = args.get("job_id", "system_check_job")

            if action == "pause":
                self.scheduler.pause_job(job_id)
                # 同步到 DB
                if self.task_mgr:
                    from models import update_task_status
                    update_task_status(job_id, "paused")
                return f"[后台指令执行成功] 已成功暂停内部任务 {job_id}。它将停止在后台活动。"

            elif action == "resume":
                self.scheduler.resume_job(job_id)
                if self.task_mgr:
                    from models import update_task_status
                    update_task_status(job_id, "running")
                return f"[后台指令执行成功] 已成功恢复内部后台任务 {job_id}。巡检系统再次激活。"

            elif action in ["status", "list"]:
                if self.task_mgr:
                    tasks = self.task_mgr.list_tasks(source="internal")
                    if not tasks:
                        return "当前没有配置任何内置后台任务。"
                    res = "[Agent内置心跳/巡检任务列表]:\n"
                    for t in tasks:
                        state = t.status.upper()
                        res += f" - 任务 ID: {t.id} | 名称: {t.name} | 状态: {state}\n"
                    return res
                else:
                    jobs = self.scheduler.get_jobs()
                    if not jobs:
                        return "当前没有配置任何内置后台任务。"
                    res = "[Agent内置心跳/巡检任务列表]:\n"
                    for j in jobs:
                        state = "PAUSED" if not j.next_run_time else "RUNNING"
                        res += f" - 任务 ID: {j.id} | 状态: {state}\n"
                    return res
            else:
                return "不支持的内部操作"
        except Exception as e:
            return f"执行崩溃: {e}"

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["pause", "resume", "status", "list"],
                            "description": "对内置系统体检任务的操作。pause 为挂起，resume 为重新激活，list 为查看列表"
                        }
                    },
                    "required": ["action"]
                }
            }
        }
