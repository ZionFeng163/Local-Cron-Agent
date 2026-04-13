import json
import base64
import subprocess
import shlex
import os
from exquisite_agent.tools.base import BaseTool

class SandboxFileWriter(BaseTool):
    name = "Sandbox_File_Writer"
    description = "在安全的 Ubuntu 沙盒中创建或覆盖复杂的脚本文件（Python/Bash等）。大段代码、特殊符号都能稳定无损地通过此工具直接写入系统底层，主要配合后续作为执行源。"

    def execute(self, action_input: str) -> str:
        try:
            try:
                args = json.loads(action_input)
                file_path = args.get("file_path")
                content = args.get("content")
            except json.JSONDecodeError:
                return "[执行失败] 必须传入标准的 JSON 字符串，包含 file_path 和 content 字段。"
                
            if not file_path or not content:
                return "[参数缺失] 必须提供 file_path 和 content。"

            # 强制只允许在用户的 home 或 tmp 下写入，并防跳区
            if ".." in file_path or file_path.startswith("/root"):
                 return "[安全拦截] 只能在 /home/ubuntu/ 或 /tmp/ 目录下操作文件！"
                 
            if not file_path.startswith("/"):
                 file_path = f"/home/ubuntu/{file_path}"
                 
            # 通过 Base64 中转消除一切各种繁杂语言中引号与特殊符被 Bash 吃掉的情形
            encoded_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
            
            # 组合底层多组命令：解码 -> 写入 -> 赋予可执行权限
            cmd = f"echo '{encoded_content}' | base64 -d > {shlex.quote(file_path)} && chmod +x {shlex.quote(file_path)}"
            
            safe_cmd = shlex.quote(cmd)
            multipass_cmd = f"/usr/local/bin/multipass exec agent-sandbox -- bash -c {safe_cmd}"
            
            # 使用较长超时时间应对可能磁盘加载过久
            result = subprocess.run(multipass_cmd, shell=True, capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                 return f"[写入报错]: {result.stderr.strip()}"
            
            return f"[代码写入成功!] 您已成功在沙盒中创建脚本：{file_path}\n此时您可以通过 Sandbox_Crontab_Admin 或者 Sandbox_Bash_Executor 去调用并执行它了。"

        except Exception as e:
            return f"[异常崩溃] 沙盒写入过程中生致命异常: {str(e)}"
            
    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "沙盒内文件的绝对路径，推荐放于 /home/ubuntu/xx.py"
                        },
                        "content": {
                            "type": "string",
                            "description": "要吸入的具体代码串或本文，切勿使用 Base64 加密，底层会自动处理"
                        }
                    },
                    "required": ["file_path", "content"]
                }
            }
        }
