"""Layered memory loading node."""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage

from agent.node_utils import record_trace_event
from agent.state import AgentState

log = logging.getLogger(__name__)


def memory_node(state: AgentState) -> dict[str, Any]:
    """Load profile, summary, long-term, and OpenViking context."""
    thread_id = state.get("thread_id", "")
    if not thread_id:
        return {}

    from services.memory import get_summary, get_user_profile
    from services.memory_store import get_memory_store

    result: dict[str, Any] = {}
    profile = get_user_profile(thread_id)
    if profile:
        parts = []
        identity = profile.get("identity", "")
        focus = profile.get("focus_areas", [])
        if identity:
            parts.append(f"身份：{identity}")
        if focus:
            parts.append(f"关注领域：{'、'.join(focus)}")
        if parts:
            result["memory_profile"] = "\n".join(parts)

    summary = get_summary(thread_id)
    if summary:
        result["memory_summary"] = summary

    messages = state.get("messages", [])
    latest_query = ""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            latest_query = message.content
            break

    if latest_query:
        try:
            store = get_memory_store()
            owner_id = str(state.get("user_id") or "").strip() or None
            try:
                relevant_memories = store.search_memories(
                    latest_query,
                    thread_id=None if owner_id else thread_id,
                    owner_id=owner_id,
                    top_k=3,
                )
            except TypeError:
                # Compatibility for custom/test stores using the former API.
                relevant_memories = store.search_memories(latest_query, top_k=3)
            if relevant_memories:
                result["memory_longterm"] = "\n".join(
                    f"- [{memory.memory_type}] {memory.content}"
                    for memory in relevant_memories
                )
        except Exception as exc:
            log.debug("长期记忆检索跳过: %s", exc)

        try:
            from services.openviking_context import retrieve_agent_context

            viking_result = retrieve_agent_context(
                latest_query,
                thread_id=thread_id,
                profile=result.get("memory_profile"),
                summary=result.get("memory_summary"),
                longterm=result.get("memory_longterm"),
            )
            if viking_result.prompt:
                result["viking_context"] = viking_result.prompt
                result["viking_context_hits"] = [
                    hit.to_dict() for hit in viking_result.hits
                ]
                record_trace_event(
                    state.get("trace_id"),
                    "viking_context_retrieval",
                    name="openviking_context_database",
                    payload={
                        "total": len(viking_result.hits),
                        "hits": result["viking_context_hits"],
                    },
                )
        except Exception as exc:
            log.debug("OpenViking 风格上下文检索跳过: %s", exc)

    return result
