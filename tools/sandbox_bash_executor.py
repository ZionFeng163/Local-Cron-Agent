import subprocess
import json
import shlex
from exquisite_agent.tools.base import BaseTool


class SandboxBashExecutor(BaseTool):
    # 直接在这里定义类属性，完美兼容各种底层的 Pydantic 或普通 Class
    name = "Sandbox_Bash_Executor"
    description = "在受限的 Ubuntu 虚拟机沙盒中执行终端命令。可用于感知环境、处理文件或写脚本。严格禁止执行破坏宿主机的命令。"

    def __init__(self):
        # 初始化自己的特有属性即可，如果底层不需要初始化参数，连 super() 都可以省略
        self.forbidden_cmds = ["rm -rf /", "mkfs", "reboot", "shutdown", "crontab -e"]
        # 容易因为等待用户输入而导致模型执行永久挂起超时的指令
        self.interactive_bins = ["vi", "vim", "nano", "top", "htop", "less", "more"]

    def execute(self, action_input: str) -> str:
        try:
            try:
                args = json.loads(action_input)
                cmd = args.get("command", action_input)
            except json.JSONDecodeError:
                cmd = action_input

            for forbidden in self.forbidden_cmds:
                if forbidden in cmd:
                    return f"[安全拦截] 警告：禁止执行包含 '{forbidden}' 的高危或非法命令！"
            
            cmd_parts = cmd.strip().split()
            if cmd_parts and cmd_parts[0] in self.interactive_bins:
                return f"[安全拦截] 警告：禁止直接调用交互式程序 '{cmd_parts[0]}'！Agent不支持全屏输入态，会导致严重卡死发生崩溃！"

            print(f"\n[SandboxBashExecutor ⚡] 正在 Multipass 沙盒中执行: {cmd}")

            # 🚀 使用 multipass exec 把命令打进 Ubuntu 沙盒
            safe_cmd = shlex.quote(cmd)
            multipass_cmd = f"/usr/local/bin/multipass exec agent-sandbox -- bash -c {safe_cmd}"

            result = subprocess.run(
                multipass_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=15
            )

            output = result.stdout
            if result.stderr:
                output += f"\n[标准错误输出]:\n{result.stderr}"

            max_length = 2000
            if len(output) > max_length:
                return output[:max_length] + f"\n\n...[⚠️ 输出过长，已被系统强行截断！请使用 grep 命令。]"

            return output if output.strip() else "[执行成功，无终端输出]"

        except subprocess.TimeoutExpired:
            return "[执行失败] 命令运行超时（超过15秒），已被系统强制终止。"
        except Exception as e:
            return f"[执行崩溃] 发生未知错误: {str(e)}"

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "要在终端执行的具体 bash 命令"}
                    },
                    "required": ["command"]
                }
            }
        }