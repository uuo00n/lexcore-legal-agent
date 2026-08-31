"""Case Analysis Agent, evolved from the former fact sufficiency agent."""
from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.node_utils import compatibility_dependency, latest_human_message, record_trace_event
from agent.prompts import CASE_ANALYSIS_SYSTEM_PROMPT
from agent.reports import build_agent_report
from agent.state import AgentState
from agent.tool_loop import apply_tool_call_budget
from agent.tools import CASE_ANALYSIS_TOOLS
from services.answer_format import strip_answer_markdown
from services.legal_analysis import build_follow_up_response, classify_legal_intent, should_ask_follow_up
from services.llm import get_llm, supports_tools


def _extract_json(content: str) -> dict[str, Any] | None:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _case_sources(state: AgentState) -> list[dict[str, Any]]:
    return [
        *list(state.get("retrieved_cases", []) or []),
        *list(state.get("retrieved_laws", []) or []),
    ]


def _case_report(
    state: AgentState,
    *,
    status: str,
    summary: str,
    findings: dict[str, Any],
    confidence: str = "medium",
    **extra: Any,
) -> dict[str, Any]:
    return build_agent_report(
        state,
        "case_analysis_agent",
        summary=summary,
        findings=findings,
        sources=_case_sources(state),
        confidence=confidence,
        status=status,
        **extra,
    )


async def _llm_case_follow_up(
    state: AgentState,
    latest_query: str,
    decision: dict[str, Any],
) -> str:
    fallback = build_follow_up_response(latest_query)
    try:
        llm_factory = compatibility_dependency("get_llm", get_llm)
        llm = llm_factory(
            provider=os.getenv("CASE_ANALYSIS_AGENT_PROVIDER", os.getenv("FACT_AGENT_PROVIDER", "deepseek")),
            model=os.getenv("CASE_ANALYSIS_AGENT_MODEL", os.getenv("FACT_AGENT_MODEL", "deepseek-v4-flash-vision-exp")),
            model_route="case_analysis_agent",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0.2,
            streaming=False,
        )
        response = await llm.ainvoke([
            SystemMessage(content="你是案件分析智能体。只生成 1-3 个补齐关键事实的问题，不给法律结论。"),
            HumanMessage(content=json.dumps({
                "用户问题": latest_query,
                "缺失维度": decision.get("facts", {}).get("missing_dimensions", []),
                "建议问题": decision.get("questions", []),
            }, ensure_ascii=False)),
        ])
        return (response.content or "").strip() or fallback
    except Exception as exc:
        record_trace_event(state.get("trace_id"), "agent_fallback", name="case_analysis_agent", payload={"error": str(exc)})
        return fallback


async def case_analysis_agent_node(state: AgentState) -> dict[str, Any]:
    """Extract and structure a case without taking over final consultation."""
    latest_query = latest_human_message(state)
    if not latest_query:
        return {"needs_follow_up": False}

    if not classify_legal_intent(latest_query).get("is_legal"):
        report = _case_report(
            state,
            status="non_legal",
            summary="未识别到需要案件分析的法律事实",
            findings={
                "facts": [], "timeline": [], "parties": [], "legal_relationships": [],
                "disputed_issues": [], "claims_and_defenses": [], "evidence_gaps": [],
                "suggested_questions": [],
            },
            confidence="high",
        )
        return {"needs_follow_up": False, "agent_reports": [report]}

    decision = should_ask_follow_up(latest_query, has_uploaded_doc=bool(state.get("uploaded_doc_text")))
    if decision["should_ask"]:
        response = await _llm_case_follow_up(state, latest_query, decision)
        questions = decision.get("questions", [])
        findings = {
            "facts": [],
            "timeline": [],
            "parties": [],
            "legal_relationships": [],
            "disputed_issues": [],
            "claims_and_defenses": [],
            "evidence_gaps": decision.get("facts", {}).get("missing_dimensions", []),
            "suggested_questions": questions,
        }
        report = _case_report(
            state,
            status="needs_more_facts",
            summary=decision.get("reason", "关键事实不足，需要补充"),
            findings=findings,
            suggested_questions=questions,
            missing_facts=findings["evidence_gaps"],
            draft_response=response,
        )
        return {"needs_follow_up": True, "agent_reports": [report]}

    context = {
        "task_id": state.get("current_step") or state.get("trace_id") or "current-request:case_analysis_agent",
        "query": latest_query,
        "uploaded_document": (state.get("uploaded_doc_text") or "")[:12000],
        "existing_reports": state.get("agent_reports", []) or [],
    }
    llm_factory = compatibility_dependency("get_llm", get_llm)
    llm = llm_factory(
        provider=os.getenv("CASE_ANALYSIS_AGENT_PROVIDER", "deepseek"),
        model=os.getenv("CASE_ANALYSIS_AGENT_MODEL", "deepseek-v4-flash-vision-exp"),
        model_route="case_analysis_agent",
        trace_id=state.get("trace_id"),
        thread_id=state.get("thread_id"),
        temperature=0.1,
        streaming=False,
    )
    tool_support = compatibility_dependency("supports_tools", supports_tools)
    if tool_support() and hasattr(llm, "bind_tools"):
        llm = llm.bind_tools(CASE_ANALYSIS_TOOLS)
    response = await llm.ainvoke([
        SystemMessage(content=CASE_ANALYSIS_SYSTEM_PROMPT),
        *list(state.get("messages", [])),
        HumanMessage(content=json.dumps(context, ensure_ascii=False)),
    ])
    if getattr(response, "tool_calls", None):
        response, tool_call_count, failure = apply_tool_call_budget(
            response,
            state,
            agent_name="case_analysis_agent",
        )
        return {
            "messages": [response],
            "tool_call_count": tool_call_count,
            "tool_loop_failure": failure,
        }

    parsed = _extract_json(response.content or "") or {}
    findings = parsed.get("findings")
    if not isinstance(findings, dict):
        findings = {
            "facts": [], "timeline": [], "parties": [], "legal_relationships": [],
            "disputed_issues": [], "claims_and_defenses": [], "evidence_gaps": [],
            "analysis_note": strip_answer_markdown(response.content or ""),
        }
    report = _case_report(
        state,
        status=str(parsed.get("status") or "facts_sufficient"),
        summary=str(parsed.get("summary") or "案件事实与争议结构已整理"),
        findings=findings,
        confidence=str(parsed.get("confidence") or "medium"),
    )
    record_trace_event(state.get("trace_id"), "agent_report", name="case_analysis_agent", payload={"status": report["status"]})
    return {"needs_follow_up": False, "agent_reports": [report]}


async def fact_agent_node(state: AgentState) -> dict[str, Any]:
    """Compatibility alias for callers using the former node name."""
    return await case_analysis_agent_node(state)


async def fact_check_node(state: AgentState) -> dict[str, Any]:
    """Compatibility alias for the former fact-check node name."""
    return await case_analysis_agent_node(state)
