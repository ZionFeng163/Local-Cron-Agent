import logging
import re
import threading
import uuid
from typing import Annotated, Dict, List, Optional, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from redis_state import redis_state
from streaming_fc_agent import StreamingFCAgent

logger = logging.getLogger(__name__)


class PlannedStep(BaseModel):
    step_id: int
    task: str
    worker_key: Literal["cron", "script", "ops", "research", "general"]
    worker_name: str = ""
    objective: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    result: str = ""


class LeaderPlan(BaseModel):
    steps: List[PlannedStep]


class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    plan: List[Dict[str, object]]
    current_step_idx: int
    execution_context: str
    step_results: List[Dict[str, object]]
    final_answer: str
    run_id: str


class LangGraphOrchestrator:
    """
    LangGraph Leader-Worker orchestrator:
    - Leader produces a structured worker plan
    - LangGraph conditional edges dispatch to explicit worker nodes
    - Workers own domain tools and stream progress in real time
    """

    def __init__(self, tools, task_mgr):
        self.tools = tools
        self.task_mgr = task_mgr
        from exquisite_agent.llm import LLM  # local import to keep startup path stable

        self.worker_agents = self._build_worker_agents(LLM)
        self.leader_llm = LLM()
        self.worker_confidence_threshold = 0.55
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
                "你是 CronWorker，专注管理脚本型定时任务。只能通过 Sandbox_Crontab_Admin 查询、添加、删除或启停 /home/ubuntu/.lca/scripts/*.sh 脚本任务，禁止创建任意 shell command cron。",
            ),
            "script": self._make_worker(
                llm_cls,
                "ScriptWorker",
                ["Sandbox_File_Writer", "Sandbox_Bash_Executor"],
                "你是 ScriptWorker，专注生成、写入和检查 Shell 脚本。必须使用 Sandbox_File_Writer 写入 /home/ubuntu/.lca/scripts/*.sh；不负责挂载 cron。",
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
                "你是 GeneralWorker，负责无法明确归类的综合任务。涉及定时任务时仍必须遵守：只能调度 /home/ubuntu/.lca/scripts/*.sh 脚本。",
            ),
        }

    def _extract_json_text(self, content: str) -> str:
        if not content:
            return ""
        text = content.strip()
        if text.startswith("{") and text.endswith("}"):
            return text

        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
        if fenced:
            return fenced.group(1)

        obj_match = re.search(r"(\{.*\})", text, flags=re.S)
        if obj_match:
            return obj_match.group(1)
        return ""

    def _parse_leader_plan(self, content: str) -> LeaderPlan:
        json_text = self._extract_json_text(content)
        if not json_text:
            raise ValueError("empty leader plan json")
        return LeaderPlan.model_validate_json(json_text)

    def _worker_name(self, worker_key: str) -> str:
        worker = self.worker_agents.get(worker_key, self.worker_agents["general"])
        return worker.name

    def _normalize_plan(self, plan: LeaderPlan) -> List[Dict[str, object]]:
        normalized = []
        for idx, step in enumerate(plan.steps[:5], start=1):
            data = step.model_dump()
            worker_key = str(data.get("worker_key") or "general")
            if worker_key not in self.worker_agents or float(data.get("confidence") or 0) < self.worker_confidence_threshold:
                worker_key = "general"
            data["step_id"] = idx
            data["worker_key"] = worker_key
            data["worker_name"] = self._worker_name(worker_key)
            data["status"] = "pending"
            data["result"] = ""
            normalized.append(data)
        return normalized or self._fallback_plan("执行用户请求")

    def _fallback_plan(self, user_input: str) -> List[Dict[str, object]]:
        text = user_input.strip() or "执行用户请求"
        return [
            {
                "step_id": 1,
                "task": text,
                "worker_key": "general",
                "worker_name": self._worker_name("general"),
                "objective": text,
                "confidence": 0.5,
                "reason": "leader_plan_fallback",
                "status": "pending",
                "result": "",
            }
        ]

    def _leader_plan(self, user_input: str) -> List[Dict[str, object]]:
        sys_prompt = """
你是 Local-Cron-Agent 的 Leader Agent，只负责规划和分派，不执行工具。
请把用户请求拆成 1-5 个可执行步骤，并为每步指定 worker_key。
worker_key 只能是:
- script: 生成、写入、检查 /home/ubuntu/.lca/scripts/*.sh 脚本
- cron: 查询、添加、删除、启停脚本型 cron 任务
- ops: 系统巡检、服务管理、即时诊断和恢复
- research: 外部资料检索
- general: 信息不足或混合任务兜底

重要约束:
- 沙盒定时任务只能调度 /home/ubuntu/.lca/scripts/*.sh。
- 如果用户要“写脚本并定时运行”，必须拆成 script 步骤和 cron 步骤。
- 只输出 JSON，不要输出任何额外文本。
输出格式:
{"steps":[{"step_id":1,"task":"...","worker_key":"script","worker_name":"ScriptWorker","objective":"...","confidence":0.9,"reason":"..."}]}
""".strip()
        user_prompt = f"用户请求: {user_input}\n请输出结构化执行计划。"
        try:
            resp = self.leader_llm.chat(
                [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}],
                [],
            )
            return self._normalize_plan(self._parse_leader_plan(getattr(resp, "content", "") or ""))
        except Exception as e:
            logger.warning("Leader plan fallback: %s", e)
            return self._fallback_plan(user_input)

    def _extract_callback(self, config: Optional[RunnableConfig]):
        if not config:
            return None
        configurable = config.get("configurable", {})
        return configurable.get("callback")

    def _emit_status(self, cb, content: str):
        if cb:
            cb({"type": "status", "content": content})

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("leader", self.leader_node)
        builder.add_node("cron_worker", self._make_worker_node("cron"))
        builder.add_node("script_worker", self._make_worker_node("script"))
        builder.add_node("ops_worker", self._make_worker_node("ops"))
        builder.add_node("research_worker", self._make_worker_node("research"))
        builder.add_node("general_worker", self._make_worker_node("general"))
        builder.add_node("solver", self.solver_node)

        worker_edges = {
            "cron": "cron_worker",
            "script": "script_worker",
            "ops": "ops_worker",
            "research": "research_worker",
            "general": "general_worker",
            "done": "solver",
        }

        builder.add_edge(START, "leader")
        builder.add_conditional_edges("leader", self.dispatch_next_worker, worker_edges)
        for node_name in ["cron_worker", "script_worker", "ops_worker", "research_worker", "general_worker"]:
            builder.add_conditional_edges(node_name, self.dispatch_next_worker, worker_edges)
        builder.add_edge("solver", END)
        return builder.compile()

    def leader_node(self, state: AgentState, config: Optional[RunnableConfig] = None):
        logger.info("--- LEADER ---")
        cb = self._extract_callback(config)
        user_input = state["messages"][0].content
        plan = self._leader_plan(user_input)
        redis_state.set_run_status(
            state["run_id"],
            {"status": "planned", "total_steps": len(plan), "current_step": 0, "plan": plan},
        )
        plan_lines = [f"{step['step_id']}. {step['worker_name']}: {step['task']}" for step in plan]
        self._emit_status(cb, "🧭 Leader 已生成执行计划：\n" + "\n".join(plan_lines))
        return {
            "plan": plan,
            "current_step_idx": 0,
            "execution_context": "",
            "step_results": [],
            "messages": [AIMessage(content=f"🏗️ [Leader 规划] 共 {len(plan)} 步")],
        }

    def _make_worker_node(self, worker_key: str):
        def worker_node(state: AgentState, config: Optional[RunnableConfig] = None):
            idx = state["current_step_idx"]
            plan = list(state["plan"])
            step = dict(plan[idx])
            worker = self.worker_agents.get(worker_key, self.worker_agents["general"])
            cb = self._extract_callback(config)
            task = str(step.get("task") or "")
            objective = str(step.get("objective") or task)
            context = (state.get("execution_context") or "")[-1600:]

            logger.info("--- %s STEP %s ---", worker.name, idx + 1)
            step["status"] = "running"
            plan[idx] = step
            redis_state.set_run_status(
                state["run_id"],
                {
                    "status": "worker_running",
                    "current_step": idx + 1,
                    "total_steps": len(plan),
                    "worker": worker.name,
                    "worker_key": worker_key,
                    "task": task,
                    "objective": objective,
                },
            )
            self._emit_status(cb, f"🚀 步骤 {idx + 1}/{len(plan)} 交给 {worker.name}: {task}")

            worker_prompt = (
                f"当前步骤: {task}\n"
                f"步骤目标: {objective}\n"
                f"Leader 分派原因: {step.get('reason', '')}\n"
                f"历史执行上下文:\n{context}\n\n"
                "请只完成当前步骤。涉及定时任务时，必须使用 /home/ubuntu/.lca/scripts/*.sh 脚本路径。"
            )

            result_parts = []
            try:
                for chunk in worker.run_stream(worker_prompt):
                    if cb:
                        cb(chunk)
                    if chunk.get("type") in {"content_chunk", "message", "tool_result"}:
                        content = chunk.get("content", "")
                        if content:
                            result_parts.append(content)
                result = "\n".join(result_parts).strip()
                step["status"] = "completed"
                step["result"] = result
            except Exception as exc:  # noqa: BLE001
                result = f"[Worker执行异常] {exc}"
                step["status"] = "failed"
                step["result"] = result
                self._emit_status(cb, result)

            plan[idx] = step
            compact_result = result[:900] + "..." if len(result) > 900 else result
            new_context = (
                (state.get("execution_context") or "")
                + f"\n[步骤 {idx + 1}][{worker.name}] {task}\n结果: {compact_result}\n"
            )[-3000:]
            step_results = list(state.get("step_results") or [])
            step_results.append(
                {
                    "step_id": step.get("step_id", idx + 1),
                    "worker": worker.name,
                    "task": task,
                    "status": step["status"],
                    "result": compact_result,
                }
            )
            redis_state.set_run_status(
                state["run_id"],
                {
                    "status": "step_completed",
                    "current_step": idx + 1,
                    "total_steps": len(plan),
                    "worker": worker.name,
                    "task": task,
                    "step_status": step["status"],
                },
            )

            if self.task_mgr:
                self.task_mgr.sync_internal_tasks()
                threading.Thread(target=self.task_mgr.sync_sandbox_tasks, daemon=True).start()

            return {
                "plan": plan,
                "execution_context": new_context,
                "step_results": step_results,
                "current_step_idx": idx + 1,
                "messages": [AIMessage(content=f"⚙️ [{worker.name} 步骤 {idx + 1} 完成]")],
            }

        return worker_node

    def solver_node(self, state: AgentState, config: Optional[RunnableConfig] = None):
        logger.info("--- SOLVER ---")
        cb = self._extract_callback(config)
        results = state.get("step_results") or []
        lines = []
        for item in results:
            status_text = "完成" if item.get("status") == "completed" else "失败"
            lines.append(f"- [{status_text}] {item.get('worker')}: {item.get('task')}")
        final_answer = "✅ 多 Agent Leader-Worker 执行完成。"
        if lines:
            final_answer += "\n" + "\n".join(lines)
        redis_state.set_run_status(state["run_id"], {"status": "completed", "results": results})
        self._emit_status(cb, "📝 正在收尾并返回结果...")
        return {
            "final_answer": final_answer,
            "messages": [AIMessage(content=final_answer)],
        }

    def dispatch_next_worker(self, state: AgentState):
        idx = state.get("current_step_idx", 0)
        plan = state.get("plan") or []
        if idx >= len(plan):
            return "done"
        step = plan[idx]
        worker_key = str(step.get("worker_key") or "general")
        if worker_key not in self.worker_agents:
            return "general"
        return worker_key

    def run_stream(self, user_input: str, callback: callable = None, run_id: str = ""):
        run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        redis_state.set_run_status(run_id, {"status": "received", "input": user_input[:500]})
        logger.info(">>> LangGraph Leader-Worker Orchestration Triggered <<<")
        initial_state = {
            "messages": [HumanMessage(content=user_input)],
            "plan": [],
            "current_step_idx": 0,
            "execution_context": "",
            "step_results": [],
            "final_answer": "",
            "run_id": run_id,
        }
        config: RunnableConfig = {"configurable": {"callback": callback}}

        for _event in self.graph.stream(initial_state, config=config, stream_mode="updates"):
            # All user-facing stream chunks already go through callback from nodes.
            pass

        yield {"type": "message", "content": "✨ 全程编排任务执行完毕。"}
