import logging
import re
import threading
from typing import Annotated, Dict, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from streaming_fc_agent import StreamingFCAgent

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    task_list: List[str]
    current_task_idx: int
    execution_context: str
    final_answer: str


class LangGraphOrchestrator:
    """
    Lightweight orchestrator:
    - Keep the UX of "multi-step execution"
    - Remove expensive planner/reflector/solver extra LLM calls
    - Stream tool/content progress from worker in real time
    """

    def __init__(self, tools, task_mgr):
        self.tools = tools
        self.task_mgr = task_mgr
        from exquisite_agent.llm import LLM  # local import to keep startup path stable

        self.worker_agents = self._build_worker_agents(LLM)
        self.worker_agent = self.worker_agents["general"]
        self.graph = self._build_graph()

    def _pick_tools(self, tool_names: List[str]):
        name_set = set(tool_names)
        return [tool for tool in self.tools if tool.name in name_set]

    def _make_worker(self, llm_cls, name: str, tool_names: List[str], prompt: str):
        worker = StreamingFCAgent(llm=llm_cls(), name=name, tools=self._pick_tools(tool_names))
        worker.system_prompt = prompt
        return worker

    def _build_worker_agents(self, llm_cls) -> Dict[str, StreamingFCAgent]:
        return {
            "cron": self._make_worker(
                llm_cls,
                "CronWorker",
                ["Sandbox_Crontab_Admin", "Agent_Heartbeat_Controller"],
                "你是 CronWorker，专注处理定时任务的创建、查询、暂停、恢复和删除。优先使用结构化任务管理工具。",
            ),
            "script": self._make_worker(
                llm_cls,
                "ScriptWorker",
                ["Sandbox_File_Writer", "Sandbox_Bash_Executor", "Sandbox_Crontab_Admin"],
                "你是 ScriptWorker，专注生成、写入、检查脚本，并在需要时将脚本挂载为定时任务。",
            ),
            "ops": self._make_worker(
                llm_cls,
                "OpsWorker",
                ["Sandbox_Health_Scanner", "Sandbox_Service_Manager", "Sandbox_Bash_Executor"],
                "你是 OpsWorker，专注系统巡检、服务管理、命令执行和异常恢复。执行前注意安全边界。",
            ),
            "research": self._make_worker(
                llm_cls,
                "ResearchWorker",
                ["Search"],
                "你是 ResearchWorker，专注外部信息查询和资料整理。",
            ),
            "general": self._make_worker(
                llm_cls,
                "GeneralWorker",
                [tool.name for tool in self.tools],
                "你是 GeneralWorker，负责无法明确归类的综合任务，可以根据需要调用任意可用工具。",
            ),
        }

    def _route_task(self, task: str) -> str:
        text = task.lower()

        cron_keywords = [
            "cron", "crontab", "定时", "任务", "调度", "暂停", "恢复", "启动", "删除",
            "每隔", "每天", "每小时", "每分钟", "心跳",
        ]
        script_keywords = [
            "脚本", "script", ".py", ".sh", "写入", "生成代码", "创建文件", "文件", "保存",
            "挂载", "部署",
        ]
        script_intent_keywords = ["写", "生成", "创建", "保存", "编写"]
        ops_keywords = [
            "健康", "巡检", "检查", "cpu", "内存", "磁盘", "负载", "服务", "systemctl",
            "重启", "修复", "异常", "报错", "日志", "执行命令", "bash",
        ]
        research_keywords = ["搜索", "查询资料", "新闻", "联网", "google", "资料", "查一下"]

        if any(kw in text for kw in script_keywords) and any(kw in text for kw in script_intent_keywords):
            return "script"

        scores = {
            "cron": sum(1 for kw in cron_keywords if kw in text),
            "script": sum(1 for kw in script_keywords if kw in text),
            "ops": sum(1 for kw in ops_keywords if kw in text),
            "research": sum(1 for kw in research_keywords if kw in text),
        }
        route, score = max(scores.items(), key=lambda item: item[1])
        return route if score > 0 else "general"

    def _get_worker(self, route: str) -> StreamingFCAgent:
        return self.worker_agents.get(route, self.worker_agents["general"])

    def _extract_callback(self, config: Optional[RunnableConfig]):
        if not config:
            return None
        configurable = config.get("configurable", {})
        return configurable.get("callback")

    def _emit_status(self, cb, content: str):
        if cb:
            cb({"type": "status", "content": content})

    def _build_plan(self, user_input: str) -> List[str]:
        """
        Cheap rule-based planner to avoid extra LLM round trip + token cost.
        """
        text = user_input.strip()
        if not text:
            return ["执行用户请求"]

        # Normalize common separators for CN/EN mixed text.
        normalized = text
        for sep in ["然后", "接着", "并且", "再", ";", "；", "\n"]:
            normalized = normalized.replace(sep, "。")

        parts = [p.strip() for p in re.split(r"[。.!?]", normalized) if p.strip()]
        if not parts:
            return [text]

        # Limit step count to keep latency controlled.
        steps = parts[:3]
        return steps

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("planner", self.planner_node)
        builder.add_node("executor", self.executor_node)
        builder.add_node("solver", self.solver_node)

        builder.add_edge(START, "planner")
        builder.add_edge("planner", "executor")
        builder.add_conditional_edges(
            "executor",
            self.should_continue_executing,
            {"next": "executor", "done": "solver"},
        )
        builder.add_edge("solver", END)
        return builder.compile()

    def planner_node(self, state: AgentState, config: Optional[RunnableConfig] = None):
        logger.info("--- PLANNER ---")
        cb = self._extract_callback(config)
        user_input = state["messages"][0].content
        task_list = self._build_plan(user_input)
        self._emit_status(cb, f"🧭 已规划 {len(task_list)} 个步骤，开始执行。")
        return {
            "task_list": task_list,
            "current_task_idx": 0,
            "execution_context": "",
            "messages": [AIMessage(content=f"🏗️ [任务规划] 共 {len(task_list)} 步")],
        }

    def executor_node(self, state: AgentState, config: Optional[RunnableConfig] = None):
        idx = state["current_task_idx"]
        task = state["task_list"][idx]
        logger.info(f"--- EXECUTOR {idx + 1} ---")

        cb = self._extract_callback(config)
        route = self._route_task(task)
        worker = self._get_worker(route)
        self._emit_status(cb, f"🚀 步骤 {idx + 1}/{len(state['task_list'])}: {task}")
        self._emit_status(cb, f"🧩 已路由至 {worker.name}")

        executor_res = ""
        for chunk in worker.run_stream(task):
            if cb:
                cb(chunk)
            if chunk.get("type") == "content_chunk":
                executor_res += chunk["content"]

        # Keep context bounded to avoid growth.
        compact_res = executor_res.strip()
        if len(compact_res) > 800:
            compact_res = compact_res[:800] + "..."
        new_context = state["execution_context"] + f"\n[步骤 {idx + 1}] {task}\n结果: {compact_res}\n"
        if len(new_context) > 2400:
            new_context = new_context[-2400:]

        if self.task_mgr:
            self.task_mgr.sync_internal_tasks()
            # Run sandbox sync async to avoid blocking stream.
            threading.Thread(target=self.task_mgr.sync_sandbox_tasks, daemon=True).start()

        return {
            "execution_context": new_context,
            "current_task_idx": idx + 1,
            "messages": [AIMessage(content=f"⚙️ [步骤 {idx + 1} 完成]")],
        }

    def solver_node(self, state: AgentState, config: Optional[RunnableConfig] = None):
        logger.info("--- SOLVER ---")
        cb = self._extract_callback(config)
        self._emit_status(cb, "📝 正在收尾并返回结果...")
        return {
            "final_answer": "✅ 多步骤任务执行完成。",
            "messages": [AIMessage(content="✅ 多步骤任务执行完成。")],
        }

    def should_continue_executing(self, state: AgentState):
        if state["current_task_idx"] < len(state["task_list"]):
            return "next"
        return "done"

    def run_stream(self, user_input: str, callback: callable = None):
        # Fast-track for short instructions
        if len(user_input.strip()) < 25:
            logger.info(">>> Fast-Track Triggered: Skipping LangGraph Planning <<<")
            route = self._route_task(user_input)
            worker = self._get_worker(route)
            if callback:
                callback({"type": "status", "content": f"🧩 已路由至 {worker.name}"})
            for chunk in worker.run_stream(user_input):
                yield chunk
            if self.task_mgr:
                self.task_mgr.sync_internal_tasks()
                threading.Thread(target=self.task_mgr.sync_sandbox_tasks, daemon=True).start()
            yield {"type": "message", "content": "✅ 指令已快速执行完毕。"}
            return

        logger.info(">>> LangGraph Orchestration Triggered: Complex Mode <<<")
        initial_state = {
            "messages": [HumanMessage(content=user_input)],
            "task_list": [],
            "current_task_idx": 0,
            "execution_context": "",
            "final_answer": "",
        }
        config: RunnableConfig = {"configurable": {"callback": callback}}

        for _event in self.graph.stream(initial_state, config=config, stream_mode="updates"):
            # All user-facing stream chunks already go through callback from nodes.
            pass

        yield {"type": "message", "content": "✨ 全程编排任务执行完毕。"}
