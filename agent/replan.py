"""Agent Replan 的独立预算；不得与 HTTP/LLM 传输重试共用计数。"""
from __future__ import annotations

from agent.state import AgentState

MAX_AGENT_REPLAN_RETRIES = 1


def replan_retry_count(state: AgentState) -> int:
    """读取新字段，并兼容阶段二十三之前的 checkpoint。"""
    value = state.get("replan_retry_count")
    if value is None:
        value = state.get("verifier_retry_count", 0)
    return max(0, int(value or 0))


__all__ = ["MAX_AGENT_REPLAN_RETRIES", "replan_retry_count"]
