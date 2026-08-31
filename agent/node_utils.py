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
