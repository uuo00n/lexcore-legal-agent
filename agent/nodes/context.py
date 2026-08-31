"""Conversation context helpers and compaction node."""
from __future__ import annotations

from typing import Any

from agent.node_utils import latest_human_message, record_trace_event
from agent.state import AgentState
from services.context_compaction import compact_state_context


async def context_compaction_node(state: AgentState) -> dict[str, Any]:
    """Compact checkpoint context before memory loading and routing."""
    result = await compact_state_context(state)
    status = result.get("context_status")
    if status:
        record_trace_event(
            state.get("trace_id"),
            "context_status",
            name="context_compaction",
            payload=status,
        )
    if result.get("context_compacted"):
        record_trace_event(
            state.get("trace_id"),
            "context_compaction",
            name="context_compaction",
            payload=status or {},
        )
    return result
