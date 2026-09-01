"""Query preparation nodes."""
from __future__ import annotations

import re
from typing import Any

from agent.node_utils import latest_human_message, record_trace_event
from agent.state import AgentState


def query_rewrite_node(state: AgentState) -> dict[str, Any]:
    """Normalize the latest user query and preserve its unmodified form."""
    original_query = latest_human_message(state).strip()
    rewritten_query = re.sub(r"\s+", " ", original_query).strip()
    record_trace_event(
        state.get("trace_id"),
        "query_rewrite",
        name="query_rewrite",
        payload={
            "input_chars": len(original_query),
            "output_chars": len(rewritten_query),
            "changed": rewritten_query != original_query,
        },
    )
    return {
        "original_query": original_query,
        "rewritten_query": rewritten_query,
    }
