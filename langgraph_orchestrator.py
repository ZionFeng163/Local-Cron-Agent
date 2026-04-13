import logging
import json
import re
from typing import Annotated, TypedDict, List, Union
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph.message import add_messages

from exquisite_agent.agents.react_fc import FCAgent
from exquisite_agent.llm import LLM

# 日志配置
logger = logging.getLogger(__name__)

# ========== 提示词（体验平衡版） ==========

PLAN_PROMPT = """你是一个务实的运维助理。用户的任务是: {user_input}
你的目标是：用最少的步骤完成任务。

原则：
1. 只输出核心步骤，严禁画蛇添足。
2. 保持专业，可以适当使用 emoji 但严禁刷屏。
3. 严禁自报家门（不要说“我是架构师”等）。

请严格输出 JSON 数组格式（如 [ "动作1" ]），不要有其他文字。"""

REFLECTION_PROMPT = """你是一个冷酷的审计员。
原始需求: {user_input}
当前结果: {current_draft}

如果过程由于冗长或结果有误，请在首行回复 FAIL 并给出建议。
如果通过，请回复：PERFECT"""

SOLVE_PROMPT = """请根据原始任务和执行记录，给出一个专业且精炼的总结。
原始任务: {user_input}
执行记录: {execution_context}"""

# 1. 定义状态 (State)
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    task_list: List[str]
    current_task_idx: int
    execution_context: str
    reflection_count: int
    is_finished: bool
    final_answer: str

# 2. 定义编排器
class LangGraphOrchestrator:
    def __init__(self, tools, task_mgr):
        self.llm = LLM()
        self.tools = tools
        self.task_mgr = task_mgr
        self.worker_agent = FCAgent(llm=self.llm, name="Worker", tools=self.tools)
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AgentState)
        
        builder.add_node("planner", self.planner_node)
        builder.add_node("executor", self.executor_node)
        builder.add_node("reflector", self.reflector_node)
        builder.add_node("solver", self.solver_node)
        
        builder.add_edge(START, "planner")
        builder.add_edge("planner", "executor")
        
        builder.add_conditional_edges(
            "executor",
            self.should_continue_executing,
            {
                "next": "executor",
                "done": "reflector"
            }
        )
        
        builder.add_conditional_edges(
            "reflector",
            self.should_replan,
            {
                "replan": "planner",
                "finish": "solver"
            }
        )
        builder.add_edge("solver", END)
        return builder.compile()

    # --- 节点逻辑 ---

    def planner_node(self, state: AgentState):
        logger.info("--- PLANNER ---")
        user_input = state["messages"][0].content
        res = self.llm.chat([{"role": "user", "content": PLAN_PROMPT.format(user_input=user_input)}])
        content = res.content
        
        try:
            clean_json = re.sub(r'```json|```', '', content).strip()
            task_list = json.loads(clean_json)
        except:
            task_list = [user_input]
            
        return {
            "task_list": task_list,
            "current_task_idx": 0,
            "execution_context": "",
            "messages": [AIMessage(content=f"🏗️ [任务规划]\n" + "\n".join([f"- {t}" for t in task_list]))]
        }

    def executor_node(self, state: AgentState, config: dict = None):
        # 修复：LangGraph 在某些环境可能传 state 或者 (state, config)
        # 兼容性处理
        idx = state["current_task_idx"]
        task = state["task_list"][idx]
        logger.info(f"--- EXECUTOR {idx+1} ---")
        
        # 获取回调
        cb = None
        if config and isinstance(config, dict):
            cb = config.get("configurable", {}).get("callback")
        
        executor_res = ""
        for chunk in self.worker_agent.run_stream(task):
            if cb:
                cb(chunk)
            if chunk.get("type") == "content_chunk":
                executor_res += chunk["content"]
        
        new_context = state["execution_context"] + f"\n[任务 {idx+1}] {task}\n结果: {executor_res}\n"
        
        if self.task_mgr:
            self.task_mgr.sync_internal_tasks()
            self.task_mgr.sync_sandbox_tasks()
        
        return {
            "execution_context": new_context,
            "current_task_idx": idx + 1,
            "messages": [AIMessage(content=f"⚙️ [步骤 {idx+1} 完成]\n{executor_res}")]
        }

    def reflector_node(self, state: AgentState):
        logger.info("--- REFLECTOR ---")
        user_input = state["messages"][0].content
        context = state["execution_context"]
        
        res = self.llm.chat([{"role": "user", "content": REFLECTION_PROMPT.format(user_input=user_input, current_draft=context)}])
        content = res.content
        
        count = state.get("reflection_count", 0) + 1
        
        if "PERFECT" in content.upper() or count >= 2:
            return {
                "is_finished": True,
                "messages": [AIMessage(content="🛡️ [审计通过]")]
            }
        else:
            return {
                "is_finished": False,
                "reflection_count": count,
                "messages": [AIMessage(content=f"⚠️ [审计打回 ({count})]\n{content}")]
            }

    def solver_node(self, state: AgentState):
        logger.info("--- SOLVER ---")
        user_input = state["messages"][0].content
        context = state["execution_context"]
        
        res = self.llm.chat([{"role": "user", "content": SOLVE_PROMPT.format(user_input=user_input, execution_context=context)}])
        return {
            "final_answer": res.content,
            "messages": [AIMessage(content=f"📝 [最终结果]\n{res.content}")]
        }

    def should_continue_executing(self, state: AgentState):
        if state["current_task_idx"] < len(state["task_list"]):
            return "next"
        return "done"

    def should_replan(self, state: AgentState):
        if state["is_finished"]:
            return "finish"
        return "replan"

    def run_stream(self, user_input: str, callback: callable = None):
        # --- “快车道” 启发式逻辑 ---
        # 如果输入长度短于 25 个字符，认定为简单指令，跳过 LangGraph 规划
        if len(user_input.strip()) < 25:
            logger.info(">>> Fast-Track Triggered: Skipping LangGraph Planning <<<")
            total_res = ""
            for chunk in self.worker_agent.run_stream(user_input):
                if callback:
                    callback(chunk)
                if chunk.get("type") == "content_chunk":
                    total_res += chunk["content"]
                yield chunk
            
            # 结束后同步一次 DB
            if self.task_mgr:
                self.task_mgr.sync_internal_tasks()
                self.task_mgr.sync_sandbox_tasks()
            yield {"type": "message", "content": "✅ 指令已快速执行完毕。"}
            return

        # --- 正常 LangGraph 流程 ---
        logger.info(">>> LangGraph Orchestration Triggered: Complex Mode <<<")
        initial_state = {
            "messages": [HumanMessage(content=user_input)],
            "task_list": [],
            "current_task_idx": 0,
            "execution_context": "",
            "reflection_count": 0,
            "is_finished": False,
            "final_answer": ""
        }
        
        config = {"configurable": {"callback": callback}}
        
        for event in self.graph.stream(initial_state, config=config, stream_mode="updates"):
            node_name = list(event.keys())[0] if event else None
            data = event.get(node_name, {})
            
            if "messages" in data and data["messages"]:
                last_msg = data["messages"][-1]
                if isinstance(last_msg, AIMessage):
                    yield {"type": "content_chunk", "content": last_msg.content + "\n\n"}
            
            if node_name == "executor":
                yield {"type": "tool_ended"}
        
        yield {"type": "message", "content": "✨ 全程编排任务执行完毕。"}
