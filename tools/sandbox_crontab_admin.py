import subprocess
import json
import shlex
from exquisite_agent.tools.base import BaseTool
from script_policy import normalize_script_path, script_name_from_path


class SandboxCrontabAdmin(BaseTool):
    name = "Sandbox_Crontab_Admin"
    description = "用于管理沙盒脚本型定时任务(Crontab)。只允许添加 /home/ubuntu/.lca/scripts/*.sh 脚本任务，支持 list/add/delete/clear。"

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
                        res += f" - [{status}] {t.cron_expr} {t.script_path}\n"
                    return res
                else:
                    # 退化到直接读沙盒
                    out = self._run_multipass("crontab -l", fail_ok=True)
                    if "no crontab for" in out.lower() or out.strip() == "":
                        return "[Cron列表为空] 当前系统中未配置任何定时任务。"
                    return f"[当前系统中的定时任务列表]:\n{out}"

            elif action == "add":
                cron_expr = args.get("expression")
                script_path = args.get("script_path") or args.get("command")
                name = args.get("task_name")
                description = args.get("description")
                
                if not cron_expr or not script_path:
                    return "[参数错误] add 操作必须提供 'expression' (定时规则) 和 'script_path' (.sh 脚本路径)。"

                normalized_script_path = normalize_script_path(script_path)
                if not normalized_script_path:
                    return "[安全拦截] 定时任务只允许挂载 /home/ubuntu/.lca/scripts/*.sh 脚本。请先用 Sandbox_File_Writer 写入脚本。"

                if self.task_mgr:
                    # 通过 TaskManager 创建（DB 优先 + 异步推送到沙盒）
                    if not name:
                        name = script_name_from_path(normalized_script_path)
                    
                    task = self.task_mgr.create_task(
                        name=name,
                        source="sandbox",
                        cron_expr=cron_expr,
                        script_path=normalized_script_path,
                        description=description or f"由 AI Agent 基于脚本 {shlex.quote(normalized_script_path)} 创建"
                    )
                    return f"[Cron添加成功] 已成功将脚本型定时任务写入系统:\n名称: {name}\n规则: {cron_expr}\n脚本: {normalized_script_path}"
                else:
                    new_entry = f"{cron_expr} bash {normalized_script_path}"
                    safe_entry = shlex.quote(new_entry)
                    sh_cmd = f"(crontab -l 2>/dev/null; echo {safe_entry}) | crontab -"
                    res = self._run_multipass(sh_cmd)
                    if "报错" in res:
                        return f"[Cron添加失败] {res}"
                    return f"[Cron添加成功] 已成功将脚本型定时任务写入后台系统:\n{new_entry}"

            elif action == "delete":
                task_id = args.get("task_id")
                if not task_id:
                     # 尝试按命令匹配
                     script_to_del = args.get("script_path") or args.get("command")
                     if script_to_del and self.task_mgr:
                         normalized_script = normalize_script_path(script_to_del) or script_to_del.strip()
                         all_sbox = self.task_mgr.list_tasks(source="sandbox")
                         for t in all_sbox:
                             if normalized_script == t.script_path:
                                 task_id = t.id
                                 break
                
                if not task_id:
                    return "[参数错误] delete 操作必须提供 'task_id' 或准确的 'script_path'。"

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
                            "description": "兼容字段，仅允许传 /home/ubuntu/.lca/scripts/*.sh 脚本路径"
                        },
                        "script_path": {
                            "type": "string",
                            "description": "要定时执行的 .sh 脚本路径，必须位于 /home/ubuntu/.lca/scripts/"
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
