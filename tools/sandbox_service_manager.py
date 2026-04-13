import subprocess
import json
import shlex
from exquisite_agent.tools.base import BaseTool

class SandboxServiceManager(BaseTool):
    name = "Sandbox_Service_Manager"
    description = "使用 systemctl 安全地管理系统的服务（启动、停止、重启、查看状态）。可用于尝试拉起僵死的服务进程。"
    
    def execute(self, action_input: str) -> str:
        try:
            args = json.loads(action_input)
            action = args.get("action", "status")
            service = args.get("service")
        except:
            return "[参数错误] 必须传入 JSON 包含 action 和 service 字段。"
            
        if not service:
            return "[执行失败] 必须提供服务名称 service (如 nginx)。"
            
        if service in ["ssh", "sshd", "networkd", "NetworkManager", "multipass"]:
             return f"[安全拦截] 严禁利用重启 {service} 这种可能导致宿主机或虚拟机失联的基础服务。"
             
        if action not in ["start", "stop", "restart", "status"]:
             return f"[执行失败] 不支持的 action: {action}"
             
        cmd = f"sudo systemctl {action} {service}"
        safe_cmd = shlex.quote(cmd)
        multipass_cmd = f"multipass exec agent-sandbox -- bash -c {safe_cmd}"
        try:
             result = subprocess.run(multipass_cmd, shell=True, capture_output=True, text=True, timeout=15)
             out = result.stdout.strip() + "\n" + result.stderr.strip()
             return f"[执行成功] {out}" if result.returncode == 0 else f"[服务操作返回信息] {out}"
        except Exception as e:
             return f"[执行崩溃] {str(e)}"
             
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
                            "enum": ["start", "stop", "restart", "status"],
                            "description": "对服务进行的操作枚举"
                        },
                        "service": {
                             "type": "string",
                             "description": "目标服务名称，例如 nginx、redis"
                        }
                    },
                    "required": ["action", "service"]
                }
            }
        }
