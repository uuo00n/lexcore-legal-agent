"""Legal consultation agent and citation/report helpers."""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.node_utils import compatibility_dependency, record_trace_event
from agent.prompts import LEGAL_SYSTEM_PROMPT, LEGAL_SYSTEM_PROMPT_NO_TOOLS
from agent.reports import build_agent_report, report_agent_name
from agent.state import AgentState
from agent.tool_loop import admit_tool_calls
from agent.tools import LEGAL_CONSULT_TOOLS
from services.answer_format import strip_answer_markdown
from services.case_retrieval import search_similar_cases
from services.context_builder import build_model_context
from services.llm import get_llm, supports_tools
from services.model_routing import select_model_route

_LAW_CITATION_RE = re.compile(
    r"《([^》]+)》\s*"
    r"(第[一二三四五六七八九十百千万亿零〇两\d]+条(?:之[一二三四五六七八九十百千万亿零〇两\d]+)?)"
)
# 句末标点与换行都算句子边界；切分后保留标点，便于原样重组。
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。；;！!？?\n])")


def _normalize_law_name(name: str) -> str:
    return re.sub(r"\s+", "", name).replace("中华人民共和国", "")


def _law_key(law_name: str, article_no: str) -> tuple[str, str]:
    return (_normalize_law_name(law_name), re.sub(r"\s+", "", article_no))


def _guard_law_citations(content: str, laws: list[dict]) -> str:
    """兼容保留的法条引用过滤器：整句丢弃未检索到的引用（§P2）。

    旧实现把未命中的引用替换成「（未在本轮检索结果中确认的法条引用已移除）」，
    用户于是在答复里看到一条疤痕，而不是一句可读的话（§二 问题 10）。现在改成
    整句丢弃——句子是能保持语义完整的最小单位。

    专家报告本身不再调用它：报告必须如实保留模型写下的引用，才能让确定性
    Citation Verifier 判出 ``citation_invalid`` 并交给 Repair Router 局部修复
    （§十四、§P0-5）；用户侧的保护改由 Answer Generator 的引用审计负责。
    """
    if not content:
        return content
    allowed = {
        _law_key(item.get("law_name", ""), item.get("article_no", ""))
        for item in laws
        if item.get("law_name") and item.get("article_no")
    }

    def is_grounded(sentence: str) -> bool:
        return all(
            _law_key(law_name, article_no) in allowed
            for law_name, article_no in _LAW_CITATION_RE.findall(sentence)
        )

    kept = [
        sentence
        for sentence in _SENTENCE_SPLIT_RE.split(content)
        if sentence.strip() and is_grounded(sentence)
    ]
    return "".join(kept).strip()


def _format_law_sources(laws: list[dict]) -> str:
    """Format retrieved laws as the legacy concise source appendix."""
    if not laws:
        return ""
    seen: set[tuple[str, str]] = set()
    sources: list[str] = []
    for item in laws:
        law_name = item.get("law_name", "")
        article_no = item.get("article_no", "")
        key = (law_name, article_no)
        if key not in seen:
            seen.add(key)
            sources.append(f"《{law_name}》{article_no}")
    lines = "\n".join(sources)
    return f"\n\n---\n【引用法条】\n{lines}"


def _extract_json_object(content: str) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def _law_basis_from_retrieval(laws: list[dict]) -> list[dict[str, str]]:
    basis: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in laws:
        law_name = str(item.get("law_name") or "")
        article_no = str(item.get("article_no") or "")
        key = (law_name, article_no)
        if not law_name or not article_no or key in seen:
            continue
        seen.add(key)
        basis.append({
            "law_name": law_name,
            "article_no": article_no,
            "point": str(item.get("content") or "")[:160],
        })
    return basis


def _build_legal_agent_report(content: str, state: AgentState) -> dict[str, Any]:
    retrieved = state.get("retrieved_laws", []) or []
    parsed = _extract_json_object(content)
    # 报告如实保留模型写下的引用：删掉可疑引用会让 Citation Verifier 看不到问题，
    # 也就无法判出 citation_invalid 并触发局部修复（§十四、§P0-5、§二 问题 10）。
    if parsed is None:
        analysis = strip_answer_markdown(content)
        detail: dict[str, Any] = {
            "status": "analysis_ready",
            "legal_issues": [],
            "law_basis": _law_basis_from_retrieval(retrieved),
            "analysis": analysis,
            "risks": [],
            "next_steps": [],
            "raw_response": content,
            "confidence": "medium",
        }
    else:
        detail = dict(parsed.get("findings") or parsed)
        detail.setdefault("status", parsed.get("status", "analysis_ready"))
        detail.setdefault("legal_issues", [])
        detail.setdefault("law_basis", _law_basis_from_retrieval(retrieved))
        detail.setdefault("risks", [])
        detail.setdefault("next_steps", [])
        detail.setdefault("confidence", parsed.get("confidence", "medium"))
        if isinstance(detail.get("analysis"), str):
            detail["analysis"] = strip_answer_markdown(detail["analysis"])
        detail["raw_response"] = content
    detail["retrieved_law_count"] = len(retrieved)
    detail.setdefault(
        "evidence_insufficient",
        bool(state.get("evidence_insufficient", False)),
    )
    summary = str((parsed or {}).get("summary") or detail.get("analysis") or "法律解释与行动建议已整理")
    report_sources = [*retrieved, *list(state.get("retrieved_cases", []) or [])]
    for specialist_report in state.get("agent_reports", []) or []:
        report_sources.extend(specialist_report.get("sources", []) or [])
    extra = {
        key: value for key, value in detail.items()
        if key not in {"agent", "agent_name", "task_id", "report_id", "confidence", "summary", "sources", "findings"}
    }
    report = build_agent_report(
        state,
        "legal_consult_agent",
        summary=summary[:500],
        findings=detail,
        sources=report_sources,
        confidence=str((parsed or {}).get("confidence") or detail.get("confidence") or "medium"),
        **extra,
    )
    return report


def _limit_tool_calls(response: AIMessage, *, max_calls: int = 1) -> AIMessage:
    calls = list(getattr(response, "tool_calls", None) or [])
    if len(calls) <= max_calls:
        return response
    try:
        response.tool_calls = calls[:max_calls]
    except Exception:
        return AIMessage(content=response.content or "", tool_calls=calls[:max_calls])
    return response


def _has_used_local_law_tool(state: AgentState) -> bool:
    if state.get("retrieved_laws"):
        return True
    for message in state.get("messages", []):
        if not isinstance(message, ToolMessage):
            continue
        if getattr(message, "name", None) in {
            "retrieve_local_law_tool",
            "legal_search_tool",
        }:
            return True
        try:
            payload = (
                json.loads(message.content)
                if isinstance(message.content, str)
                else message.content
            )
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if (
            "results" in payload
            and "status" in payload
            and ("score_threshold" in payload or "top_rerank_score" in payload)
        ):
            return True
    return False


def _legal_consult_tools_for_state(state: AgentState) -> list[Any]:
    tools = list(LEGAL_CONSULT_TOOLS)
    has_statute_report = any(
        report_agent_name(report) == "statute_retrieval_agent"
        and not report.get("evidence_insufficient", False)
        for report in state.get("agent_reports", []) or []
    )
    has_case_report = any(
        report_agent_name(report) == "case_analysis_agent"
        for report in state.get("agent_reports", []) or []
    )
    if has_statute_report:
        tools = [tool for tool in tools if tool.name not in {"retrieve_local_law_tool", "search_law_tool"}]
    elif _has_used_local_law_tool(state):
        tools = [
            tool
            for tool in tools
            if tool.name != "retrieve_local_law_tool"
        ]
    if has_case_report:
        tools = [tool for tool in tools if tool.name != "search_case_tool"]
    return tools


async def legal_consult_agent_node(state: AgentState) -> dict[str, Any]:
    """Run the memory-aware legal consultation and trusted-tool loop."""
    tool_support = compatibility_dependency("supports_tools", supports_tools)
    base_prompt = LEGAL_SYSTEM_PROMPT if tool_support() else LEGAL_SYSTEM_PROMPT_NO_TOOLS

    latest_user = ""
    for item in reversed(state.get("messages", []) or []):
        if isinstance(item, HumanMessage):
            latest_user = item.content
            break

    route_selector = compatibility_dependency("select_model_route", select_model_route)
    route = route_selector(
        user_message=latest_user,
        doc_text=state.get("uploaded_doc_text"),
        tool_call_count=state.get("tool_call_count", 0),
    )
    task_context = {
        "task_id": state.get("current_step") or state.get("trace_id") or "current-request:legal_consult_agent",
        "query": state.get("rewritten_query") or latest_user,
    }
    built = build_model_context(state, base_prompt, task_context=task_context)
    record_trace_event(
        state.get("trace_id"),
        "model_route",
        name=route.name,
        payload={
            "route": route.name,
            "provider": route.provider,
            "model": route.model,
            "reason": route.reason,
            "complexity_score": route.complexity_score,
        },
    )

    llm_factory = compatibility_dependency("get_llm", get_llm)
    llm = llm_factory(
        provider=route.provider,
        model=route.model,
        model_route=route.name,
        trace_id=state.get("trace_id"),
        thread_id=state.get("thread_id"),
    )
    if tool_support(route.provider):
        llm = llm.bind_tools(_legal_consult_tools_for_state(state))

    record_trace_event(
        state.get("trace_id"),
        "context_build",
        name="legal_consult_agent",
        payload=built.status,
    )
    response = await llm.ainvoke(built.messages)
    if getattr(response, "tool_calls", None):
        response = _limit_tool_calls(response)
    step = admit_tool_calls(response, state, agent_name="legal_consult_agent")
    if step.continue_loop:
        result: dict[str, Any] = {**step.updates, "context_build_status": built.status}
        record_trace_event(
            state.get("trace_id"),
            "agent_tool_request",
            name="legal_consult_agent",
            payload={
                "tools": [
                    call.get("name", "")
                    for call in (getattr(result["messages"][0], "tool_calls", None) or [])
                ]
            },
        )
        return result

    # 软停止时不再检索，直接基于已有证据给出法律分析（§P1-2、§P1-3）。
    report = _build_legal_agent_report(response.content or "", state)
    record_trace_event(
        state.get("trace_id"),
        "agent_report",
        name="legal_consult_agent",
        payload={
            "status": report.get("status"),
            "retrieved_law_count": report.get("retrieved_law_count", 0),
            "analysis_preview": str(report.get("analysis") or "")[:500],
        },
    )
    return {"agent_reports": [report], "context_build_status": built.status}
