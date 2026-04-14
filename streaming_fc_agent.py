import threading
import time

from exquisite_agent.agents.react_fc import FCAgent


class StreamingFCAgent(FCAgent):
    """FCAgent with richer progress events during long tool execution."""

    def _prepare_task(self, user_input: str):
        # Important: avoid unbounded short-term-memory growth across requests.
        # This is the main source of latency/token explosion in long-lived sessions.
        self.clear_messages()
        self.memory.set_current_task(user_input)
        self.add_message("user", user_input)

    def run(self, user_input: str):
        self._prepare_task(user_input)
        tool_used = False
        for _ in range(self.max_iterations):
            message = self.llm.chat(self.get_full_messages(), self.openai_tools)
            self.add_raw_message(message.model_dump(exclude_none=True))

            if message.tool_calls:
                tool_used = True
                for tool_call in message.tool_calls:
                    func_name = tool_call.function.name
                    func_args = tool_call.function.arguments
                    tool_id = tool_call.id

                    observation = f"No Tool named {func_name}"
                    for tool in self.tools:
                        if tool.name == func_name:
                            observation = tool.execute(func_args)
                            break

                    self.add_raw_message({"role": "tool", "tool_call_id": tool_id, "content": observation})
            else:
                if tool_used and message.content:
                    self.memory.consolidate_memory(user_input, message.content)
                return message.content

    def run_stream(self, user_input: str):
        self._prepare_task(user_input)

        tool_used = False

        for _ in range(self.max_iterations):
            stream = self.llm.chat_stream(self.get_full_messages(), self.openai_tools)

            is_tool_calling = False
            tool_calls_buffer = {}
            final_content = ""

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.tool_calls:
                    is_tool_calling = True
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_buffer:
                            tc_id = tc.id if tc.id else f"call_{idx}"
                            tool_calls_buffer[idx] = {
                                "id": tc_id,
                                "type": "function",
                                "function": {"name": tc.function.name if tc.function.name else "", "arguments": ""},
                            }
                        if tc.function.name and not tool_calls_buffer[idx]["function"]["name"]:
                            tool_calls_buffer[idx]["function"]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_calls_buffer[idx]["function"]["arguments"] += tc.function.arguments
                elif delta.content and not is_tool_calling:
                    final_content += delta.content
                    yield {"type": "content_chunk", "content": delta.content}

            if is_tool_calling:
                tool_calls = [tool_calls_buffer[idx] for idx in sorted(tool_calls_buffer.keys())]

                self.add_raw_message({"role": "assistant", "content": None, "tool_calls": tool_calls})
                tool_used = True

                for tc in tool_calls:
                    func_name = tc["function"]["name"]
                    func_args = tc["function"]["arguments"]
                    tool_id = tc["id"]

                    yield {"type": "tool_start", "content": f"⚙️ 正在执行工具：{func_name}"}

                    observation_holder = {"value": f"No Tool named {func_name}"}
                    error_holder = {"value": None}

                    def _run_tool():
                        try:
                            for tool in self.tools:
                                if tool.name == func_name:
                                    observation_holder["value"] = str(tool.execute(func_args))
                                    break
                        except Exception as exc:  # noqa: BLE001
                            error_holder["value"] = str(exc)

                    start_ts = time.time()
                    worker = threading.Thread(target=_run_tool, daemon=True)
                    worker.start()

                    last_notice_sec = -1
                    while worker.is_alive():
                        elapsed = int(time.time() - start_ts)
                        # Emit keep-alive status every 2s while tool is running.
                        if elapsed >= 2 and elapsed % 2 == 0 and elapsed != last_notice_sec:
                            last_notice_sec = elapsed
                            yield {
                                "type": "status",
                                "content": f"⏳ {func_name} 执行中（{elapsed}s）...",
                            }
                        time.sleep(0.2)

                    worker.join()

                    if error_holder["value"]:
                        observation = f"[执行崩溃] {error_holder['value']}"
                    else:
                        observation = observation_holder["value"]

                    self.add_raw_message(
                        {
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": observation,
                        }
                    )

                    preview = (observation or "").strip().replace("\n", " ")
                    if preview:
                        if len(preview) > 180:
                            preview = preview[:180] + "..."
                        yield {"type": "tool_result", "content": f"✅ {func_name} 执行完成：{preview}"}
                    else:
                        yield {"type": "tool_result", "content": f"✅ {func_name} 执行完成"}

                    yield {"type": "tool_ended", "func_name": func_name}
            else:
                if tool_used and final_content:
                    self.memory.consolidate_memory(user_input, final_content)
                yield {"type": "content_end"}
                return
