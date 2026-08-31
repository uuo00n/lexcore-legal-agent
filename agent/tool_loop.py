"""Shared guardrails for bounded Specialist ReAct tool loops."""
from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from agent.state import AgentState, ToolLoopFailure


MAX_TOOL_CALLS = max(1, int(os.getenv("MAX_TOOL_CALLS", "5")))
TOOL_CALL_LIMIT_ERROR = "tool_call_limit_exceeded"


def _replace_tool_calls(response: AIMessage, calls: list[dict[str, Any]]) -> AIMessage:
    """Keep the model response while admitting only calls that fit the budget."""
    try:
        response.tool_calls = calls
        return response
    except Exception:
        return AIMessage(
            content=response.content or "",
            tool_calls=calls,
            additional_kwargs=response.additional_kwargs,
            response_metadata=response.response_metadata,
            id=response.id,
            name=response.name,
        )


def apply_tool_call_budget(
    response: AIMessage,
    state: AgentState,
    *,
    agent_name: str,
) -> tuple[AIMessage, int, ToolLoopFailure | None]:
    """Admit tool calls up to the per-task budget and describe overflow."""
    calls = list(getattr(response, "tool_calls", None) or [])
    current_count = max(0, int(state.get("tool_call_count", 0) or 0))
    if not calls:
        return response, current_count, None

    remaining = max(0, MAX_TOOL_CALLS - current_count)
    if remaining:
        admitted = calls[:remaining]
        response = _replace_tool_calls(response, admitted)
        return response, current_count + len(admitted), None

    failure: ToolLoopFailure = {
        "agent_name": agent_name,
        "task_id": str(
            state.get("current_step")
            or state.get("trace_id")
            or f"current-request:{agent_name}"
        ),
        "reason": TOOL_CALL_LIMIT_ERROR,
        "message": f"任务工具调用次数已达到上限 {MAX_TOOL_CALLS}，拒绝继续调用工具",
        "tool_call_count": current_count,
        "max_tool_calls": MAX_TOOL_CALLS,
        "requested_tools": [str(call.get("name") or "") for call in calls],
    }
    return response, current_count, failure


def tool_limit_observation_node(state: AgentState) -> dict[str, Any]:
    """Reject over-budget calls with protocol-valid error observations."""
    messages = list(state.get("messages", []) or [])
    last = messages[-1] if messages else None
    calls = list(getattr(last, "tool_calls", None) or [])
    failure = dict(state.get("tool_loop_failure") or {})
    failure.setdefault("reason", TOOL_CALL_LIMIT_ERROR)
    failure.setdefault("message", f"任务工具调用次数已达到上限 {MAX_TOOL_CALLS}")
    failure.setdefault("tool_call_count", int(state.get("tool_call_count", 0) or 0))
    failure.setdefault("max_tool_calls", MAX_TOOL_CALLS)
    failure.setdefault("requested_tools", [str(call.get("name") or "") for call in calls])

    observations = [
        ToolMessage(
            content=json.dumps(
                {
                    "status": "error",
                    "error": TOOL_CALL_LIMIT_ERROR,
                    "message": failure["message"],
                    "tool_call_count": failure["tool_call_count"],
                    "max_tool_calls": failure["max_tool_calls"],
                    "retryable": False,
                },
                ensure_ascii=False,
            ),
            tool_call_id=str(call.get("id") or f"rejected_tool_call_{index}"),
            name=str(call.get("name") or "unknown_tool"),
            status="error",
        )
        for index, call in enumerate(calls)
    ]
    result: dict[str, Any] = {"tool_loop_failure": failure}
    if observations:
        result["messages"] = observations
    return result


def tool_error_observation(error: Exception) -> str:
    """Render execution failures as observations the Specialist can react to."""
    return json.dumps(
        {
            "status": "error",
            "error": "tool_execution_error",
            "error_type": type(error).__name__,
            "message": str(error),
            "retryable": True,
            "instruction": "可调整参数、改用该 Specialist 获准的其他工具，或基于现有证据结束任务。",
        },
        ensure_ascii=False,
    )
