import subprocess
import json
import shlex
from exquisite_agent.tools.base import BaseTool


class SandboxCrontabAdmin(BaseTool):
    name = "Sandbox_Crontab_Admin"
    description = "用于管理系统定时任务(Crontab)。专门用来安全地增加、删除或查询定时任务。支持命令类型: 'list' (查看当前所有任务), 'add' (增加新任务), 'clear' (清空所有)。注意：无需手动使用 Sandbox_Bash_Executor 修改 crontab，交给我来管理更安全。"

    def __init__(self, task_mgr=None):
        self.task_mgr = task_mgr

    def execute(self, action_input: str) -> str:
        try:
            try:
                args = json.loads(action_input)
            except json.JSONDecodeError:
                return "[执行失败] Sandbox_Crontab_Admin 的参数必须是合法的 JSON 格式。必须包含 action 字段。"

            action = args.get("action")

            if action == "list":
                if self.task_mgr:
                    tasks = self.task_mgr.list_tasks(source="sandbox")
                    if not tasks:
                        return "[Cron列表为空] 当前系统中未配置任何定时任务。"
                    res = "[当前系统中的定时任务列表]:\n"
                    for t in tasks:
                        status = "⏸️暂停" if t.status == "paused" else "▶️运行中"
                        res += f" - [{status}] {t.cron_expr} {t.command}\n"
                    return res
                else:
                    # 退化到直接读沙盒
                    out = self._run_multipass("crontab -l", fail_ok=True)
                    if "no crontab for" in out.lower() or out.strip() == "":
                        return "[Cron列表为空] 当前系统中未配置任何定时任务。"
                    return f"[当前系统中的定时任务列表]:\n{out}"

            elif action == "add":
                cron_expr = args.get("expression")
                command = args.get("command")
                name = args.get("task_name")
                description = args.get("description")
                
                if not cron_expr or not command:
                    return "[参数错误] add 操作必须提供 'expression' (定时规则) 和 'command' (执行内容)。"

                if self.task_mgr:
                    # 通过 TaskManager 创建（DB 优先 + 异步推送到沙盒）
                    if not name:
                        name = command.split("/")[-1] if "/" in command else command[:30]
                    
                    task = self.task_mgr.create_task(
                        name=name,
                        source="sandbox",
                        cron_expr=cron_expr,
                        command=command,
                        description=description or f"由 AI Agent 在 {shlex.quote(command)} 基础上创建"
                    )
                    return f"[Cron添加成功] 已成功将以下定时任务写入系统:\n名称: {name}\n规则: {cron_expr}\n命令: {command}"
                else:
                    # 原有逻辑...

                    new_entry = f"{cron_expr} {command}"
                    safe_entry = shlex.quote(new_entry)
                    sh_cmd = f"(crontab -l 2>/dev/null; echo {safe_entry}) | crontab -"
                    res = self._run_multipass(sh_cmd)
                    if "报错" in res:
                        return f"[Cron添加失败] {res}"
                    return f"[Cron添加成功] 已成功将以下定时任务写入后台系统:\n{new_entry}"

            elif action == "delete":
                task_id = args.get("task_id")
                if not task_id:
                     # 尝试按命令匹配
                     cmd_to_del = args.get("command")
                     if cmd_to_del and self.task_mgr:
                         all_sbox = self.task_mgr.list_tasks(source="sandbox")
                         for t in all_sbox:
                             if cmd_to_del.strip() in t.command:
                                 task_id = t.id
                                 break
                
                if not task_id:
                    return "[参数错误] delete 操作必须提供 'task_id' 或准确的 'command'。"

                if self.task_mgr:
                    success = self.task_mgr.remove_task(task_id)
                    if success:
                        return f"[Cron删除成功] 任务 ID {task_id} 已从系统卸载并删除。"
                    else:
                        return f"[Cron删除失败] 未找到 ID 为 {task_id} 的任务。"
                else:
                    return "[执行失败] 未连接 TaskManager，无法执行精准删除。"

            elif action == "clear":
                if self.task_mgr:
                    # 从 DB 删除所有沙盒任务，异步清空 crontab
                    from models import delete_tasks_by_source
                    delete_tasks_by_source("sandbox")
                    import threading
                    threading.Thread(target=self._run_multipass, args=("crontab -r",), kwargs={"fail_ok": True}, daemon=True).start()
                    return "[清空完毕] 当前用户的所有定时任务已经被清空卸载。"
                else:
                    self._run_multipass("crontab -r", fail_ok=True)
                    return "[清空完毕] 当前用户的所有定时任务已经被清空卸载。"
            else:
                return f"[请求错误] 不支持的操作类型: {action}"

        except Exception as e:
            return f"[异常崩溃] Sandbox_Crontab_Admin 执行发生不可预知的致命错误: {str(e)}"

    def _run_multipass(self, cmd: str, fail_ok: bool = False) -> str:
        safe_cmd = shlex.quote(cmd)
        multipass_cmd = f"/usr/local/bin/multipass exec agent-sandbox -- bash -c {safe_cmd}"
        result = subprocess.run(multipass_cmd, shell=True, capture_output=True, text=True, timeout=20)
        if result.returncode != 0 and not fail_ok:
            return f"[沙盒内部报错] {result.stderr.strip()}"
        return result.stdout.strip() if result.stdout.strip() else "操作成功，无显式返回"

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
                            "enum": ["list", "add", "delete", "clear"],
                            "description": "要进行的操作"
                        },
                        "task_id": {
                            "type": "string",
                            "description": "要删除的任务 ID，动作仅在 'delete' 时建议填写"
                        },
                        "expression": {
                            "type": "string",
                            "description": "标准 Linux cron 表达式(如 '0 3 * * *')，动作仅在 'add' 时须填写"
                        },
                        "command": {
                            "type": "string",
                            "description": "要后台执行的具体系统指令或已赋予 x 权限的脚本绝对路径，仅在 'add' 时须填写"
                        },
                        "task_name": {
                            "type": "string",
                            "description": "一个简洁、人类可读的任务名称，例如 '每月备份脚本' 或 '系统自愈重启'。严禁使用原始代码作为名称。"
                        },
                        "description": {
                            "type": "string",
                            "description": "详细描述该任务到底是做什么的，解决了什么问题。例如：'每周末凌晨三点执行，清理沙盒中 /tmp 下超过 7 天的长效日志文件'。"
                        }
                    },
                    "required": ["action"]
                }
            }
        }
