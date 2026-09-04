"""Shared utilities for graph nodes and business-agent nodes."""
from __future__ import annotations

import logging
import sys
from typing import Any, TypeVar

from langchain_core.messages import HumanMessage

from agent.state import AgentState

log = logging.getLogger(__name__)
T = TypeVar("T")


def compatibility_dependency(name: str, default: T) -> T:
    """Resolve a dependency exposed by the legacy ``agent.nodes`` facade."""
    facade = sys.modules.get("agent.nodes")
    return getattr(facade, name, default) if facade is not None else default


def record_trace_event(
    trace_id: str | None,
    event_type: str,
    *,
    name: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """Record an agent event without allowing observability failures to leak."""
    if not trace_id:
        return
    try:
        from services.observability import record_event

        record_event(trace_id, event_type, name=name, payload=payload or {})
    except Exception as exc:
        log.debug("trace event skipped: %s", exc)


def latest_human_message(state: AgentState) -> str:
    """Return the latest human message content from graph state."""
    for item in reversed(state.get("messages", [])):
        if isinstance(item, HumanMessage):
            return item.content
    return ""


def effective_question(state: AgentState) -> str:
    """本轮实际需要处理的问题文本。

    普通轮次等价于 ``latest_human_message``；澄清恢复轮次里 Fact Merge 会把原始
    问题与用户补充合并后写入 ``rewritten_query``，此时必须用合并后的问题做意图与
    事实判定——否则一句「3 年」会被当成闲聊或全新问题（§八）。
    """
    merged = str(state.get("rewritten_query") or "").strip()
    return merged or latest_human_message(state).strip()
