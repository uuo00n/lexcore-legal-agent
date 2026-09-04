"""Runtime adapters used when registering LangGraph nodes."""
from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode

from agent.replan import replan_retry_count
from agent.state import AgentState
from agent.tool_loop import tool_error_observation
from services.observability import record_event, trace_context
from services.workflow_metrics import record_node_execution


def _retrieval_count(output: Any) -> int:
    if not isinstance(output, dict):
        return 0
    return sum(
        len(output.get(key) or [])
        for key in ("retrieved_laws", "retrieved_cases")
        if isinstance(output.get(key), list)
    )


def observed_node(
    node_name: str,
    node: Any,
    *,
    agent_name: str = "",
) -> Callable[..., Any]:
    """Wrap a graph node with the shared trace timeline."""

    async def run(state: AgentState, config: RunnableConfig) -> Any:
        configurable = (config or {}).get("configurable", {})
        trace_id = str(state.get("trace_id") or configurable.get("trace_id") or "")
        thread_id = str(state.get("thread_id") or configurable.get("thread_id") or "")
        resolved_agent = agent_name
        if not resolved_agent:
            failure = state.get("tool_loop_failure") or {}
            resolved_agent = str(failure.get("agent_name") or "")
        retry_count = max(
            int(state.get("retry_count", 0) or 0),
            replan_retry_count(state),
        )
        started = time.perf_counter()
        with trace_context(
            trace_id=trace_id,
            thread_id=thread_id,
            node_name=node_name,
            agent_name=resolved_agent,
            retry_count=retry_count,
        ):
            try:
                if hasattr(node, "ainvoke"):
                    output = await node.ainvoke(state, config=config)
                else:
                    output = node(state)
                    if inspect.isawaitable(output):
                        output = await output
            except Exception as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                # 指标与 Trace 分开上报：record_event 在没有 trace_id 时直接返回，
                # 而 §二十五 的节点时延不该因为某次调用没带 trace 就丢失。
                record_node_execution(node_name, latency_ms=latency_ms, success=False)
                record_event(
                    trace_id,
                    "graph_node",
                    name=node_name,
                    payload={
                        "latency_ms": latency_ms,
                        "success": False,
                        "error": str(exc),
                    },
                )
                raise
            latency_ms = int((time.perf_counter() - started) * 1000)
            record_node_execution(node_name, latency_ms=latency_ms, success=True)
            record_event(
                trace_id,
                "graph_node",
                name=node_name,
                payload={
                    "latency_ms": latency_ms,
                    "success": True,
                    "retrieval_count": _retrieval_count(output),
                    "output_keys": (
                        sorted(str(key) for key in output.keys())[:30]
                        if isinstance(output, dict)
                        else []
                    ),
                },
            )
            return output

    run.__name__ = f"observed_{node_name}"
    return run


def observed_tool_node(
    node_name: str,
    tools: list[Any],
    *,
    agent_name: str,
) -> Callable[..., Any]:
    """Build a traced ToolNode with the shared error observation policy."""
    return observed_node(
        node_name,
        ToolNode(tools, handle_tool_errors=tool_error_observation),
        agent_name=agent_name,
    )


def resolve_supervisor_nodes(
    intent_router: Callable[..., Any],
    supervisor: Callable[..., Any],
    default_supervisor: Callable[..., Any],
) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Preserve legacy graph-level Supervisor patch points for integrations."""
    if supervisor is default_supervisor:
        return intent_router, supervisor

    async def compatible_supervisor(state: AgentState) -> Any:
        if state.get("plan") and not state.get("agent_reports"):
            return await default_supervisor(state)
        return await supervisor(state)

    return supervisor, compatible_supervisor
