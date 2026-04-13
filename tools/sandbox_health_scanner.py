import subprocess
import json
import shlex
from exquisite_agent.tools.base import BaseTool

class SandboxHealthScanner(BaseTool):
    name = "Sandbox_Health_Scanner"
    description = "获取系统当前的健康状况（CPU、内存、磁盘以及最占资源的进程）。一次性返回所有核心负载指标，省去手动编写多条Bash检查的烦恼。"
    
    def execute(self, action_input: str) -> str:
        # 通过预设脚本，高效地一次性取回沙盒内的核心健康指标，防止 LLM 去猜工具参数导致失败
        cmd = """
        echo '=== 1. 磁盘情况 ==='
        df -h / /home/ubuntu 2>/dev/null | grep -v 'loop'
        echo '\n=== 2. 内存情况 (MB) ==='
        free -m
        echo '\n=== 3. 系统负载 (Load Average) ==='
        uptime
        echo '\n=== 4. 消耗 CPU 最多的前 5 个进程 ==='
        ps -eo pid,ppid,cmd,%mem,%cpu --sort=-%cpu | head -n 6
        """
        safe_cmd = shlex.quote(cmd)
        multipass_cmd = f"multipass exec agent-sandbox -- bash -c {safe_cmd}"
        try:
             result = subprocess.run(multipass_cmd, shell=True, capture_output=True, text=True, timeout=10)
             if result.returncode != 0:
                 return f"[获取指标报错]: {result.stderr}"
             return result.stdout.strip()
        except Exception as e:
             return f"[获取指标崩溃]: {str(e)}"
             
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
                            "description": "监控类型，该工具无须传强制参数，默认直接执行全量检查。"
                        }
                    }
                }
            }
        }
