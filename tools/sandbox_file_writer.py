import json
import base64
import subprocess
import shlex
from exquisite_agent.tools.base import BaseTool
from script_policy import normalize_script_path

class SandboxFileWriter(BaseTool):
    name = "Sandbox_File_Writer"
    description = "在 Ubuntu 沙盒中创建或覆盖平台管理的 Shell 脚本。只允许写入 /home/ubuntu/.lca/scripts/*.sh，并会自动做 bash 语法检查。"

    def _normalize_shell_content(self, content: str) -> str:
        text = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text.startswith("#!"):
            text = "#!/usr/bin/env bash\nset -euo pipefail\n\n" + text
        if not text.endswith("\n"):
            text += "\n"
        return text

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

            normalized_path = normalize_script_path(file_path)
            if not normalized_path:
                 return "[安全拦截] 只允许写入 /home/ubuntu/.lca/scripts/*.sh 脚本文件。"
            content = self._normalize_shell_content(content)
                 
            # 通过 Base64 中转消除一切各种繁杂语言中引号与特殊符被 Bash 吃掉的情形
            encoded_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
            
            # 解码 -> 语法检查 -> 赋予可执行权限
            safe_path = shlex.quote(normalized_path)
            cmd = (
                "mkdir -p /home/ubuntu/.lca/scripts && "
                f"echo '{encoded_content}' | base64 -d > {safe_path} && "
                f"bash -n {safe_path} && "
                f"chmod +x {safe_path}"
            )
            
            safe_cmd = shlex.quote(cmd)
            multipass_cmd = f"/usr/local/bin/multipass exec agent-sandbox -- bash -c {safe_cmd}"
            
            # 使用较长超时时间应对可能磁盘加载过久
            result = subprocess.run(multipass_cmd, shell=True, capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                 return f"[脚本写入/校验失败]: {result.stderr.strip()}"
            
            return f"[Shell脚本写入成功] 已创建并通过 bash -n 校验：{normalized_path}\n后续可通过 Sandbox_Crontab_Admin 将该脚本挂载为定时任务。"

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
                            "description": "脚本文件名或绝对路径，必须规范化到 /home/ubuntu/.lca/scripts/*.sh"
                        },
                        "content": {
                            "type": "string",
                            "description": "Shell 脚本内容，切勿使用 Base64 加密，底层会自动处理"
                        }
                    },
                    "required": ["file_path", "content"]
                }
            }
        }
