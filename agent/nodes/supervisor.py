"""Supervisor routing and final-response node."""
from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.agents.legal_consult_agent import _guard_law_citations
from agent.node_utils import (
    compatibility_dependency,
    latest_human_message,
    record_trace_event,
)
from agent.prompts import SUPERVISOR_DIRECT_PROMPT, SUPERVISOR_FINAL_PROMPT
from agent.reports import report_agent_name
from agent.state import AgentState
from services.answer_format import strip_answer_markdown
from services.legal_analysis import classify_legal_intent, score_legal_answer
from services.llm import get_llm
from services.supervisor import route_user_request_with_llm


def _last_report_from(state: AgentState, agent_name: str) -> dict[str, Any] | None:
    for report in reversed(state.get("agent_reports", []) or []):
        if report_agent_name(report) == agent_name:
            return report
    return None


def _fallback_supervisor_final_response(state: AgentState) -> str:
    reports = state.get("agent_reports", []) or []
    latest = reports[-1] if reports else {}
    findings = latest.get("findings") if isinstance(latest.get("findings"), dict) else {}
    questions = latest.get("suggested_questions") or latest.get("questions") or findings.get("suggested_questions") or []
    if latest.get("status") == "needs_more_facts" and questions:
        lines = "\n".join(
            f"{index}. {question}"
            for index, question in enumerate(questions[:3], start=1)
        )
        return f"我还需要先确认几个关键信息：\n{lines}"
    for key in ("draft_response", "final_response", "analysis", "summary"):
        value = latest.get(key)
        if isinstance(value, str) and value.strip():
            return strip_answer_markdown(value)
    if questions:
        lines = "\n".join(
            f"{index}. {question}"
            for index, question in enumerate(questions[:3], start=1)
        )
        return f"我还需要先确认几个关键信息：\n{lines}"
    return "我已经完成初步分析，但还需要你补充更多关键信息后，才能给出更稳妥的判断。"


def _trusted_laws_for_final(state: AgentState) -> list[dict[str, Any]]:
    statute_report = _last_report_from(state, "statute_retrieval_agent")
    if statute_report is not None:
        sources = statute_report.get("sources") or []
        return [item for item in sources if isinstance(item, dict)]
    return list(state.get("retrieved_laws", []) or [])


def _next_route_after_agent_reports(state: AgentState) -> tuple[str, str]:
    latest = (state.get("agent_reports", []) or [])[-1]
    agent = report_agent_name(latest)
    status = latest.get("status")
    if agent == "case_analysis_agent" and status == "needs_more_facts":
        return "end", "案件分析智能体确认关键事实不足，由主控向用户追问"
    if agent == "case_analysis_agent" and _last_report_from(state, "statute_retrieval_agent") is None:
        return "statute_retrieval_agent", "案件结构已整理，分配独立法规检索任务"
    if agent == "statute_retrieval_agent" and _last_report_from(state, "legal_consult_agent") is None:
        return "legal_consult_agent", "法规报告已完成，交由法律咨询智能体综合解释和行动建议"
    return "end", f"{agent or '专家智能体'} 已返回报告，由主控生成最终回复"


async def _llm_supervisor_final_response(state: AgentState) -> str:
    latest_query = latest_human_message(state)
    payload = {
        "用户问题": latest_query,
        "专家报告": state.get("agent_reports", []) or [],
        "检索法条": _trusted_laws_for_final(state),
        "上传文档": state.get("uploaded_doc_name") or "",
    }
    fallback = _fallback_supervisor_final_response(state)
    try:
        llm_factory = compatibility_dependency("get_llm", get_llm)
        llm = llm_factory(
            provider=os.getenv("SUPERVISOR_PROVIDER", "deepseek"),
            model=os.getenv("SUPERVISOR_MODEL", "deepseek-v4-flash-vision-exp"),
            model_route="supervisor_agent",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0.2,
            streaming=False,
        )
        response = await llm.ainvoke([
            SystemMessage(content=SUPERVISOR_FINAL_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        content = strip_answer_markdown((response.content or "").strip()) or fallback
    except Exception as exc:
        record_trace_event(
            state.get("trace_id"),
            "agent_fallback",
            name="supervisor_agent",
            payload={"error": str(exc)},
        )
        content = fallback

    retrieved = _trusted_laws_for_final(state)
    if retrieved:
        content = _guard_law_citations(content, retrieved)
        content = strip_answer_markdown(content)
    return content


async def _llm_supervisor_direct_response(state: AgentState, reason: str) -> str:
    latest_query = latest_human_message(state)
    fallback = "我在，你慢慢说。可以先告诉我发生了什么，或者你现在最想解决哪件事。"
    payload = {"用户输入": latest_query, "路由理由": reason}
    try:
        llm_factory = compatibility_dependency("get_llm", get_llm)
        llm = llm_factory(
            provider=os.getenv("SUPERVISOR_PROVIDER", "deepseek"),
            model=os.getenv("SUPERVISOR_MODEL", "deepseek-v4-flash-vision-exp"),
            model_route="supervisor_agent",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0.3,
            streaming=False,
        )
        response = await llm.ainvoke([
            SystemMessage(content=SUPERVISOR_DIRECT_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        return strip_answer_markdown((response.content or "").strip()) or fallback
    except Exception as exc:
        record_trace_event(
            state.get("trace_id"),
            "agent_fallback",
            name="supervisor_agent",
            payload={"error": str(exc)},
        )
        return fallback


async def supervisor_agent_node(state: AgentState) -> dict[str, Any]:
    """Route work to business agents and synthesize their final response."""
    reports = state.get("agent_reports", []) or []
    if reports:
        route, reason = _next_route_after_agent_reports(state)
        if route != "end":
            record_trace_event(
                state.get("trace_id"),
                "supervisor_route",
                name="supervisor_agent",
                payload={"route": route, "reason": reason, "from_reports": True},
            )
            return {
                "supervisor_route": route,
                "supervisor_reason": reason,
                "supervisor_finalized": False,
            }
        final_content = await _llm_supervisor_final_response(state)
        record_trace_event(
            state.get("trace_id"),
            "final_answer",
            name="supervisor_agent",
            payload={
                "content_preview": final_content[:500],
                "answer_score": score_legal_answer(
                    latest_human_message(state),
                    final_content,
                    state.get("retrieved_laws", []),
                ),
            },
        )
        return {
            "supervisor_route": "end",
            "supervisor_reason": reason,
            "supervisor_finalized": True,
            "messages": [AIMessage(content=final_content)],
        }

    latest_query = latest_human_message(state)
    route_request = compatibility_dependency(
        "route_user_request_with_llm",
        route_user_request_with_llm,
    )
    decision = await route_request(
        message=latest_query,
        has_uploaded_doc=bool(state.get("uploaded_doc_text")),
        uploaded_doc_name=state.get("uploaded_doc_name"),
        trace_id=state.get("trace_id"),
        thread_id=state.get("thread_id"),
    )
    payload = {
        "route": decision.route,
        "reason": decision.reason,
        "complexity": decision.complexity,
        "need_tools": decision.need_tools,
    }
    record_trace_event(
        state.get("trace_id"),
        "supervisor_route",
        name="supervisor_agent",
        payload=payload,
    )
    if decision.route == "final":
        final_content = await _llm_supervisor_direct_response(state, decision.reason)
        record_trace_event(
            state.get("trace_id"),
            "final_answer",
            name="supervisor_agent",
            payload={
                "content_preview": final_content[:500],
                "answer_score": score_legal_answer(
                    latest_query,
                    final_content,
                    state.get("retrieved_laws", []),
                ),
            },
        )
        return {
            "intent": "non_legal",
            "intent_confidence": 0.0,
            "task_complexity": decision.complexity,
            "supervisor_route": "end",
            "supervisor_reason": decision.reason,
            "supervisor_finalized": True,
            "messages": [AIMessage(content=final_content)],
        }
    detected_intent = classify_legal_intent(latest_query)
    return {
        "intent": str(detected_intent["category"]),
        "intent_confidence": float(detected_intent["confidence"]),
        "task_complexity": decision.complexity,
        "supervisor_route": decision.route,
        "supervisor_reason": decision.reason,
        "supervisor_finalized": False,
    }
