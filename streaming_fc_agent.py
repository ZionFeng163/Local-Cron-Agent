import json
import os
from typing import Any, Dict, Iterable, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import create_react_agent
from openai import OpenAI

from experience_memory import tool_observation_ok


class OpenAICompatibleChatModel(BaseChatModel):
    """Small LangChain ChatModel adapter for the project's OpenAI-compatible endpoint."""

    model_name: str
    api_key: str
    base_url: Optional[str] = None
    temperature: float = 0.1
    bound_tools: Optional[List[dict]] = None

    @property
    def _llm_type(self) -> str:
        return "local_cron_openai_compatible"

    def bind_tools(self, tools, *, tool_choice: Optional[str] = None, **kwargs: Any):
        tool_payloads = []
        for item in tools:
            if isinstance(item, dict) and item.get("type") == "function":
                tool_payloads.append(item)
            elif hasattr(item, "name") and hasattr(item, "description") and hasattr(item, "args"):
                raw_schema = getattr(item, "args_schema", None)
                required = []
                if isinstance(raw_schema, dict):
                    required = list(raw_schema.get("required") or [])
                tool_payloads.append(
                    {
                        "type": "function",
                        "function": {
                            "name": item.name,
                            "description": item.description,
                            "parameters": {
                                "type": "object",
                                "properties": item.args,
                                "required": required,
                            },
                        },
                    }
                )
            else:
                tool_payloads.append(item)
        return self.model_copy(update={"bound_tools": tool_payloads})

    def _client(self) -> OpenAI:
        return OpenAI(api_key=self.api_key, base_url=self.base_url)

    def _convert_messages(self, messages: Iterable[BaseMessage]) -> List[Dict[str, Any]]:
        converted = []
        for message in messages:
            if isinstance(message, SystemMessage):
                converted.append({"role": "system", "content": message.content})
            elif isinstance(message, HumanMessage):
                converted.append({"role": "user", "content": message.content})
            elif isinstance(message, ToolMessage):
                converted.append(
                    {
                        "role": "tool",
                        "content": str(message.content),
                        "tool_call_id": message.tool_call_id,
                    }
                )
            elif isinstance(message, AIMessage):
                item: Dict[str, Any] = {"role": "assistant", "content": message.content or ""}
                if message.tool_calls:
                    item["content"] = message.content or None
                    item["tool_calls"] = [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False),
                            },
                        }
                        for call in message.tool_calls
                    ]
                converted.append(item)
            else:
                converted.append({"role": "user", "content": str(message.content)})
        return converted

    def _message_from_openai(self, message) -> AIMessage:
        tool_calls = []
        for call in getattr(message, "tool_calls", None) or []:
            raw_args = call.function.arguments or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {"__raw_args": raw_args}
            tool_calls.append({"name": call.function.name, "args": args, "id": call.id})
        return AIMessage(content=message.content or "", tool_calls=tool_calls)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "temperature": self.temperature,
            "messages": self._convert_messages(messages),
        }
        if self.bound_tools:
            payload["tools"] = self.bound_tools
            payload["tool_choice"] = "auto"
        if stop:
            payload["stop"] = stop

        response = self._client().chat.completions.create(**payload)
        message = self._message_from_openai(response.choices[0].message)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs: Any):
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "temperature": self.temperature,
            "messages": self._convert_messages(messages),
            "stream": True,
        }
        if self.bound_tools:
            payload["tools"] = self.bound_tools
            payload["tool_choice"] = "auto"
        if stop:
            payload["stop"] = stop

        for chunk in self._client().chat.completions.create(**payload):
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = delta.content or ""
            tool_call_chunks = []
            for tc in delta.tool_calls or []:
                tool_call_chunks.append(
                    {
                        "name": tc.function.name or "",
                        "args": tc.function.arguments or "",
                        "id": tc.id or "",
                        "index": tc.index,
                    }
                )
            if content or tool_call_chunks:
                yield ChatGenerationChunk(
                    message=AIMessageChunk(content=content, tool_call_chunks=tool_call_chunks)
                )


class StreamingFCAgent:
    """LangGraph prebuilt ReAct agent with the project's existing stream event protocol."""

    def __init__(self, llm=None, name: str = "LangGraphReActAgent", tools: Optional[List[Any]] = None):
        self.name = name
        self.tools = tools or []
        self.system_prompt = f"你是一个名为 {self.name} 的智能助手。尽可能使用工具来解决用户的问题。"
        self.max_iterations = 8
        self._lc_tools = [self._wrap_tool(tool) for tool in self.tools]

    def _make_model(self) -> OpenAICompatibleChatModel:
        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            raise RuntimeError("You must set LLM_API_KEY")
        return OpenAICompatibleChatModel(
            model_name=os.getenv("LLM_MODEL_ID") or "qwen3.5-flash",
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL") or None,
            temperature=0.1,
        )

    def _wrap_tool(self, tool) -> StructuredTool:
        schema = tool.to_openai_schema()["function"]
        params = schema.get("parameters") or {"type": "object", "properties": {}}

        def _run_tool(**kwargs):
            return tool.execute(json.dumps(kwargs, ensure_ascii=False))

        return StructuredTool.from_function(
            func=_run_tool,
            name=schema["name"],
            description=schema.get("description") or tool.description,
            args_schema=params,
        )

    def _prompt_with_memory(self, sop: str = "") -> str:
        return f"{self.system_prompt}\n{sop}" if sop else self.system_prompt

    def _new_agent(self, user_input: str, sop: str = ""):
        return create_react_agent(
            self._make_model(),
            self._lc_tools,
            prompt=self._prompt_with_memory(sop),
            version="v2",
            name=self.name,
        )

    def run(self, user_input: str):
        content = ""
        for chunk in self.run_stream(user_input):
            if chunk.get("type") in {"content_chunk", "message", "tool_result"}:
                content += chunk.get("content", "")
        return content

    def run_stream(self, user_input: str, sop: str = ""):
        agent = self._new_agent(user_input, sop=sop)
        final_content = ""
        tool_trace = []
        tool_call_names: Dict[str, str] = {}
        tool_call_args: Dict[str, Any] = {}
        started_tool_call_ids = set()
        seen_ai_message_ids = set()
        seen_tool_message_ids = set()

        inputs = {"messages": [HumanMessage(content=user_input)]}
        try:
            stream = agent.stream(inputs, stream_mode=["messages", "updates"])
            for mode, chunk in stream:
                yield from self._handle_stream_chunk(
                    mode,
                    chunk,
                    final_content_ref={"value": final_content},
                    tool_trace=tool_trace,
                    tool_call_names=tool_call_names,
                    tool_call_args=tool_call_args,
                    started_tool_call_ids=started_tool_call_ids,
                    seen_ai_message_ids=seen_ai_message_ids,
                    seen_tool_message_ids=seen_tool_message_ids,
                )
                if mode == "messages":
                    message = chunk[0] if isinstance(chunk, tuple) else chunk
                    if isinstance(message, AIMessageChunk) and message.content:
                        final_content += str(message.content)
        except Exception as exc:  # noqa: BLE001
            yield {"type": "message", "content": f"[LangGraph ReAct 执行异常]: {exc}"}
            yield {"type": "content_end"}
            return

        if tool_trace and final_content:
            yield {
                "type": "experience_candidate",
                "worker": self.name,
                "task": user_input,
                "final_content": final_content,
                "tool_trace": tool_trace,
            }
        yield {"type": "content_end"}

    def _handle_stream_chunk(
        self,
        mode,
        chunk,
        final_content_ref,
        tool_trace,
        tool_call_names,
        tool_call_args,
        started_tool_call_ids,
        seen_ai_message_ids,
        seen_tool_message_ids,
    ):
        if mode == "messages":
            message = chunk[0] if isinstance(chunk, tuple) else chunk
            if isinstance(message, AIMessageChunk):
                if message.content:
                    yield {"type": "content_chunk", "content": str(message.content)}
                for tc in message.tool_call_chunks:
                    call_id = tc.get("id") or f"call_{tc.get('index', 0)}"
                    if tc.get("name"):
                        tool_call_names[call_id] = tc["name"]
                        if call_id not in started_tool_call_ids:
                            started_tool_call_ids.add(call_id)
                            yield {"type": "tool_start", "content": f"⚙️ 正在执行工具：{tc['name']}"}
                    if tc.get("args"):
                        tool_call_args[call_id] = str(tool_call_args.get(call_id, "")) + tc["args"]
            return

        if mode != "updates" or not isinstance(chunk, dict):
            return

        for _node_name, update in chunk.items():
            messages = update.get("messages", []) if isinstance(update, dict) else []
            for message in messages:
                message_id = getattr(message, "id", None) or id(message)
                if isinstance(message, AIMessage):
                    if message_id in seen_ai_message_ids:
                        continue
                    seen_ai_message_ids.add(message_id)
                    for call in message.tool_calls:
                        tool_call_names[call["id"]] = call["name"]
                        tool_call_args[call["id"]] = call.get("args") or {}
                        if call["id"] not in started_tool_call_ids:
                            started_tool_call_ids.add(call["id"])
                            yield {"type": "tool_start", "content": f"⚙️ 正在执行工具：{call['name']}"}
                elif isinstance(message, ToolMessage):
                    if message_id in seen_tool_message_ids:
                        continue
                    seen_tool_message_ids.add(message_id)
                    tool_name = tool_call_names.get(message.tool_call_id, "unknown_tool")
                    observation = str(message.content)
                    args = tool_call_args.get(message.tool_call_id, "")
                    ok = tool_observation_ok(observation)
                    tool_trace.append({"name": tool_name, "args": str(args), "observation": observation, "ok": ok})
                    preview = observation.strip().replace("\n", " ")
                    if len(preview) > 180:
                        preview = preview[:180] + "..."
                    prefix = "✅ 执行完成" if ok else "⚠️ 执行失败"
                    yield {"type": "tool_result", "content": f"{prefix}：{tool_name}：{preview}", "ok": ok}
                    yield {"type": "tool_ended", "func_name": tool_name}
