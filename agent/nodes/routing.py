"""LangGraph conditional edges and tool-result collection."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from agent.node_utils import record_trace_event
from agent.state import AgentState

log = logging.getLogger(__name__)
MAX_TOOL_CALLS = int(os.getenv("MAX_TOOL_CALLS", "4"))


def should_after_supervisor(state: AgentState) -> str:
    route = state.get("supervisor_route") or "legal_consult_agent"
    if state.get("supervisor_finalized") or route in {"end", "final"}:
        return "end"
    if route in {"fact_agent", "contract_agent", "legal_consult_agent"}:
        return route
    return "legal_consult_agent"


def should_after_fact_check(state: AgentState) -> str:
    """Compatibility conditional edge for the former graph topology."""
    return "end" if state.get("needs_follow_up") else "agent"


def should_continue(state: AgentState) -> str:
    messages = state.get("messages", [])
    if not messages:
        return "end"
    last = messages[-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        if state.get("tool_call_count", 0) >= MAX_TOOL_CALLS:
            log.warning("ReAct 循环达到上限 %d 次，强制结束", MAX_TOOL_CALLS)
            return "end"
        return "tools"
    return "end"


def collect_retrieved_laws(state: AgentState) -> dict[str, Any]:
    """Collect and deduplicate retrieved laws from tool messages."""
    messages = state.get("messages", [])
    all_laws: list[dict] = []
    seen_ids: set[str] = set()
    retrieval_attempted = False
    evidence_found = False

    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        try:
            payload = message.content
            if isinstance(payload, str):
                payload = json.loads(payload)

            items: list[dict] = []
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                if payload.get("source_type") in {
                    "local_rag",
                    "delilegal_law",
                    "delilegal_case",
                } or "evidence_insufficient" in payload:
                    retrieval_attempted = True
                    evidence_found = evidence_found or payload.get("status") == "found"
                if "results" in payload and isinstance(payload["results"], list):
                    if payload.get("source_type") == "delilegal_law":
                        for law in payload["results"]:
                            for article in law.get("relevant_articles", []):
                                items.append({
                                    "law_name": law.get("title", ""),
                                    "article_no": article.get("article_no", ""),
                                    "content": article.get("content", ""),
                                    "source_type": "delilegal_law",
                                    "source_id": law.get("id", ""),
                                    "title": law.get("title", ""),
                                    "issued_no": law.get("issued_no"),
                                    "publisher_name": law.get("publisher_name"),
                                    "publish_date": law.get("publish_date"),
                                    "active_date": law.get("active_date"),
                                    "timeliness_name": law.get("timeliness_name"),
                                    "level_name": law.get("level_name"),
                                })
                    elif payload.get("source_type") != "delilegal_case":
                        items.extend(payload["results"])
                if "relevant_laws" in payload:
                    items.extend(payload["relevant_laws"])
                if "law_a" in payload and "law_b" in payload:
                    items.extend(payload["law_a"].get("articles", []))
                    items.extend(payload["law_b"].get("articles", []))

            for item in items:
                key = f"{item.get('law_name', '')}_{item.get('article_no', '')}"
                if key not in seen_ids:
                    seen_ids.add(key)
                    all_laws.append(item)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    if all_laws:
        record_trace_event(
            state.get("trace_id"),
            "retrieval_collect",
            name="collect_laws",
            payload={"law_count": len(all_laws), "laws": all_laws[:10]},
        )
        return {"retrieved_laws": all_laws, "evidence_insufficient": False}
    if retrieval_attempted:
        return {"evidence_insufficient": not evidence_found}
    return {}
