"""Fact sufficiency agent node."""
from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.node_utils import (
    compatibility_dependency,
    latest_human_message,
    record_trace_event,
)
from agent.state import AgentState
from services.legal_analysis import build_follow_up_response, should_ask_follow_up
from services.llm import get_llm


async def _llm_fact_follow_up(
    state: AgentState,
    latest_query: str,
    decision: dict[str, Any],
) -> str:
    """Generate concise follow-up questions, falling back to rule output."""
    fallback = build_follow_up_response(latest_query)
    try:
        prompt = (
            "你是事实审查智能体。用户的法律问题事实不足，请只追问 1-3 个关键事实，"
            "不要引用法条，不要下结论，语气简洁。"
        )
        payload = {
            "用户问题": latest_query,
            "缺失事实维度": decision.get("facts", {}).get("missing_dimensions", []),
            "建议问题": decision.get("questions", []),
        }
        llm_factory = compatibility_dependency("get_llm", get_llm)
        llm = llm_factory(
            provider=os.getenv("FACT_AGENT_PROVIDER", "deepseek"),
            model=os.getenv("FACT_AGENT_MODEL", "deepseek-v4-flash-vision-exp"),
            model_route="fact_agent",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0.2,
            streaming=False,
        )
        response = await llm.ainvoke([
            SystemMessage(content=prompt),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        return (response.content or "").strip() or fallback
    except Exception as exc:
        record_trace_event(
            state.get("trace_id"),
            "agent_fallback",
            name="fact_agent",
            payload={"error": str(exc)},
        )
        return fallback


async def fact_agent_node(state: AgentState) -> dict[str, Any]:
    """Check whether a legal question has enough facts to proceed."""
    latest_query = latest_human_message(state)
    if not latest_query:
        return {"needs_follow_up": False}
    decision = should_ask_follow_up(
        latest_query,
        has_uploaded_doc=bool(state.get("uploaded_doc_text")),
    )
    record_trace_event(
        state.get("trace_id"),
        "fact_check",
        name="fact_agent",
        payload=decision,
    )
    if not decision["should_ask"]:
        return {
            "needs_follow_up": False,
            "agent_reports": [{
                "agent": "fact_agent",
                "status": "facts_sufficient",
                "summary": decision.get("reason", "事实足够进入下一步分析"),
                "missing_facts": [],
                "suggested_questions": [],
                "confidence": "medium",
            }],
        }
    response = await _llm_fact_follow_up(state, latest_query, decision)
    return {
        "needs_follow_up": True,
        "agent_reports": [{
            "agent": "fact_agent",
            "status": "needs_more_facts",
            "summary": decision.get("reason", "事实不足，需要先追问"),
            "missing_facts": decision.get("facts", {}).get("missing_dimensions", []),
            "suggested_questions": decision.get("questions", []),
            "draft_response": response,
            "confidence": "medium",
        }],
    }


async def fact_check_node(state: AgentState) -> dict[str, Any]:
    """Compatibility alias for the former fact-check node name."""
    return await fact_agent_node(state)
