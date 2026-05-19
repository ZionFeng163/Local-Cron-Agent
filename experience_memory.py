import logging
import re
import uuid
from typing import Any, Dict, List


try:
    from exquisite_agent.memory import AgentMemory
except Exception:  # noqa: BLE001
    AgentMemory = None

logger = logging.getLogger(__name__)


FAILURE_PATTERNS = [
    r"\[执行失败\]",
    r"\[异常崩溃\]",
    r"Cron添加失败",
    r"Cron删除失败",
    r"Traceback",
    r"Exception",
    r"\berror\b",
    r"exit code\s*[1-9]",
    r"退出码\s*[1-9]",
    r"失败",
]

VALUE_KEYWORDS = [
    "创建",
    "新增",
    "写入",
    "生成",
    "修复",
    "自愈",
    "安装",
    "配置",
    "调度",
    "启停",
    "启动",
    "暂停",
    "恢复",
    "部署",
    "挂载",
    "删除",
    "更新",
    "create",
    "write",
    "fix",
    "repair",
    "install",
    "configure",
    "schedule",
    "start",
    "stop",
    "deploy",
    "update",
]

QUERY_ONLY_KEYWORDS = [
    "列出",
    "查看",
    "查询",
    "打印",
    "显示",
    "检查",
    "巡检",
    "状态",
    "日志",
    "list",
    "show",
    "query",
    "check",
    "status",
    "log",
]


def tool_observation_ok(observation: str) -> bool:
    text = observation or ""
    return not any(re.search(pattern, text, flags=re.I) for pattern in FAILURE_PATTERNS)


class ExperienceMemoryPolicy:
    """Centralized policy for persisting reusable successful multi-agent experience."""

    def __init__(self):
        self.memory = None
        if AgentMemory:
            try:
                self.memory = AgentMemory()
            except Exception as exc:  # noqa: BLE001
                logger.warning("长期经验策略初始化失败，将跳过成功经验入库: %s", exc)

    def _collection(self):
        ltm = getattr(self.memory, "ltm", None)
        return getattr(ltm, "collection", None)

    def _clip_text(self, text: str, limit: int = 1200) -> str:
        text = (text or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    def _split_text_chunks(self, text: str, max_chars: int = 900) -> List[str]:
        text = (text or "").strip()
        if not text:
            return []
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()] or [text]

        chunks = []
        current = ""
        for paragraph in paragraphs:
            if len(paragraph) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""
                for start in range(0, len(paragraph), max_chars):
                    chunks.append(paragraph[start:start + max_chars].strip())
                continue

            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= max_chars:
                current = candidate
            else:
                chunks.append(current)
                current = paragraph

        if current:
            chunks.append(current)
        return chunks

    def _has_failure_text(self, text: str) -> bool:
        return not tool_observation_ok(text)

    def _is_reusable_task(self, text: str) -> bool:
        lowered = (text or "").lower()
        has_value_keyword = any(keyword.lower() in lowered for keyword in VALUE_KEYWORDS)
        has_query_keyword = any(keyword.lower() in lowered for keyword in QUERY_ONLY_KEYWORDS)
        return has_value_keyword and not (has_query_keyword and not has_value_keyword)

    def should_retrieve(self, text: str) -> bool:
        return bool(self.memory and self._collection() and self._is_reusable_task(text))

    def retrieve_sop_once(self, user_input: str, plan: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not self.should_retrieve(user_input):
            return {"retrieved": False, "sop": "", "reason": "not_reusable_task_or_memory_unavailable"}

        plan_summary = "\n".join(
            f"- {item.get('worker_name') or item.get('worker')}: {item.get('task')}"
            for item in plan
        )
        query = f"{user_input}\n\n执行计划:\n{plan_summary}".strip()
        try:
            self.memory.clear_messages()
            self.memory.set_current_task(query)
            sop = getattr(self.memory, "current_sop_prompt", "") or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("长期经验检索失败，将跳过 SOP 注入: %s", exc)
            return {"retrieved": False, "sop": "", "reason": "retrieve_failed"}

        if not sop:
            return {"retrieved": False, "sop": "", "reason": "no_relevant_sop"}
        return {"retrieved": True, "sop": sop, "reason": ""}

    def reject_reason(
        self,
        user_input: str,
        final_answer: str,
        step_results: List[Dict[str, Any]],
        candidates: List[Dict[str, Any]],
    ) -> str:
        if not self.memory or not candidates:
            return "memory_or_candidate_missing"
        if not self._collection():
            return "collection_missing"
        if not all(item.get("status") == "completed" for item in step_results):
            return "step_not_completed"
        if self._has_failure_text(final_answer):
            return "final_answer_has_failure"
        if not self._is_reusable_task(user_input):
            return "not_reusable_task"

        for candidate in candidates:
            if self._has_failure_text(candidate.get("final_content", "")):
                return "candidate_has_failure"
            for item in candidate.get("tool_trace", []):
                if not item.get("ok", False):
                    return "tool_trace_failed"
        return ""

    def should_consolidate(
        self,
        user_input: str,
        final_answer: str,
        step_results: List[Dict[str, Any]],
        candidates: List[Dict[str, Any]],
    ) -> bool:
        return not self.reject_reason(user_input, final_answer, step_results, candidates)

    def _build_chunks(
        self,
        user_input: str,
        final_answer: str,
        step_results: List[Dict[str, Any]],
        candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        lines = ["多 Agent 执行步骤:"]
        for item in step_results:
            lines.append(
                f"- [{item.get('status')}] {item.get('worker')}: "
                f"{self._clip_text(str(item.get('task') or ''), 300)}"
            )

        tool_lines = ["工具调用链路:"]
        for candidate in candidates:
            worker = candidate.get("worker", "unknown_worker")
            for item in candidate.get("tool_trace", []):
                tool_lines.append(
                    f"- Worker: {worker}\n"
                    f"  工具: {item.get('name', 'unknown_tool')}\n"
                    f"  参数: {self._clip_text(str(item.get('args') or ''), 500)}\n"
                    f"  结果: {self._clip_text(str(item.get('observation') or ''), 700)}"
                )

        body = "\n".join(lines) + "\n\n" + "\n".join(tool_lines)
        body += "\n\n最终处理结果:\n" + self._clip_text(final_answer, 1600)
        raw_chunks = self._split_text_chunks(body)
        total = len(raw_chunks)
        return [
            {
                "document": f"任务目标: {self._clip_text(user_input, 500)}\n经验片段 {idx}/{total}:\n{chunk}",
                "solution": f"[Leader-Worker 可复用 SOP 片段 {idx}/{total}]\n历史任务: {self._clip_text(user_input, 500)}\n{chunk}",
            }
            for idx, chunk in enumerate(raw_chunks, start=1)
        ]

    def consolidate_success(
        self,
        user_input: str,
        final_answer: str,
        step_results: List[Dict[str, Any]],
        candidates: List[Dict[str, Any]],
        run_id: str,
        thread_id: str,
    ) -> Dict[str, Any]:
        reason = self.reject_reason(user_input, final_answer, step_results, candidates)
        if reason:
            return {"stored": False, "chunks": 0, "reason": reason}

        collection = self._collection()
        chunks = self._build_chunks(user_input, final_answer, step_results, candidates)
        if not chunks:
            return {"stored": False, "chunks": 0, "reason": "empty_chunks"}

        base_id = uuid.uuid4().hex[:8]
        workers = sorted({str(item.get("worker") or "") for item in step_results if item.get("worker")})
        tool_names = sorted(
            {
                str(tool.get("name") or "")
                for candidate in candidates
                for tool in candidate.get("tool_trace", [])
                if tool.get("name")
            }
        )
        collection.add(
            documents=[chunk["document"] for chunk in chunks],
            metadatas=[
                {
                    "solution": chunk["solution"],
                    "source": "leader_worker_success_experience",
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "workers": ",".join(workers),
                    "tool_names": ",".join(tool_names),
                    "chunk_index": idx,
                    "chunk_total": len(chunks),
                }
                for idx, chunk in enumerate(chunks, start=1)
            ],
            ids=[f"sop_success_{base_id}_{idx}" for idx in range(1, len(chunks) + 1)],
        )
        print(f"[ExperienceMemoryPolicy] 已沉淀 {len(chunks)} 个成功经验 chunk")
        return {"stored": True, "chunks": len(chunks)}
