"""LangGraph StateGraph 构建 —— 定义 ReAct 循环的图拓扑。"""
from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from agent.nodes import (
    collect_retrieved_laws,
    case_analysis_agent_node,
    context_compaction_node,
    inject_doc_node,
    legal_consult_agent_node,
    answer_generator_node,
    memory_node,
    planner_node,
    should_enter_planner,
    should_after_verifier,
    should_continue,
    should_execute_next,
    supervisor_agent_node,
    tool_limit_observation_node,
    statute_retrieval_agent_node,
    result_verifier_node,
)
from agent.state import AgentState
from agent.tools import CASE_ANALYSIS_TOOLS, LEGAL_CONSULT_TOOLS, STATUTE_RETRIEVAL_TOOLS
from agent.tool_loop import tool_error_observation
from agent.replan import replan_retry_count
from services.observability import record_event, trace_context


def _retrieval_count(output: Any) -> int:
    if not isinstance(output, dict):
        return 0
    return sum(
        len(output.get(key) or [])
        for key in ("retrieved_laws", "retrieved_cases")
        if isinstance(output.get(key), list)
    )


def _observed_node(
    node_name: str,
    node: Any,
    *,
    agent_name: str = "",
) -> Callable[..., Any]:
    """将节点接入既有 Trace 时间线，同时保留 LangGraph RunnableConfig 传播。"""

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
                record_event(
                    trace_id,
                    "graph_node",
                    name=node_name,
                    payload={
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                        "success": False,
                        "error": str(exc),
                    },
                )
                raise
            record_event(
                trace_id,
                "graph_node",
                name=node_name,
                payload={
                    "latency_ms": int((time.perf_counter() - started) * 1000),
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


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> Any:
    """
    函数作用：
        构建 LangGraph 状态图。
    输入参数：
        - checkpointer: BaseCheckpointSaver | None，默认值 None
    输出参数：
        - Any
    """
    graph = StateGraph(AgentState)

    graph.add_node("context_compaction", _observed_node("context_compaction", context_compaction_node))
    graph.add_node("memory", _observed_node("memory", memory_node))
    graph.add_node("inject_doc", _observed_node("inject_doc", inject_doc_node))
    graph.add_node("request_router", _observed_node("request_router", supervisor_agent_node, agent_name="supervisor_agent"))
    graph.add_node("supervisor_agent", _observed_node("supervisor_agent", supervisor_agent_node, agent_name="supervisor_agent"))
    graph.add_node("planner", _observed_node("planner", planner_node))
    graph.add_node("case_analysis_agent", _observed_node("case_analysis_agent", case_analysis_agent_node, agent_name="case_analysis_agent"))
    graph.add_node("statute_retrieval_agent", _observed_node("statute_retrieval_agent", statute_retrieval_agent_node, agent_name="statute_retrieval_agent"))
    graph.add_node("legal_consult_agent", _observed_node("legal_consult_agent", legal_consult_agent_node, agent_name="legal_consult_agent"))
    graph.add_node("verifier", _observed_node("verifier", result_verifier_node))
    graph.add_node("answer_generator", _observed_node("answer_generator", answer_generator_node))
    graph.add_node(
        "case_analysis_tools",
        _observed_node(
            "case_analysis_tools",
            ToolNode(CASE_ANALYSIS_TOOLS, handle_tool_errors=tool_error_observation),
            agent_name="case_analysis_agent",
        ),
    )
    graph.add_node(
        "statute_retrieval_tools",
        _observed_node(
            "statute_retrieval_tools",
            ToolNode(STATUTE_RETRIEVAL_TOOLS, handle_tool_errors=tool_error_observation),
            agent_name="statute_retrieval_agent",
        ),
    )
    graph.add_node(
        "legal_consult_tools",
        _observed_node(
            "legal_consult_tools",
            ToolNode(LEGAL_CONSULT_TOOLS, handle_tool_errors=tool_error_observation),
            agent_name="legal_consult_agent",
        ),
    )
    graph.add_node("tool_limit_exceeded", _observed_node("tool_limit_exceeded", tool_limit_observation_node))
    graph.add_node("collect_case_evidence", _observed_node("collect_case_evidence", collect_retrieved_laws, agent_name="case_analysis_agent"))
    graph.add_node("collect_statute_evidence", _observed_node("collect_statute_evidence", collect_retrieved_laws, agent_name="statute_retrieval_agent"))
    graph.add_node("collect_consult_evidence", _observed_node("collect_consult_evidence", collect_retrieved_laws, agent_name="legal_consult_agent"))

    graph.set_entry_point("context_compaction")
    graph.add_edge("context_compaction", "memory")
    graph.add_edge("memory", "inject_doc")
    graph.add_edge("inject_doc", "request_router")
    graph.add_conditional_edges(
        "request_router",
        should_enter_planner,
        {
            "planner": "planner",
            "case_analysis_agent": "case_analysis_agent",
            "statute_retrieval_agent": "statute_retrieval_agent",
            "legal_consult_agent": "legal_consult_agent",
            "verify": "verifier",
            "end": END,
        },
    )
    graph.add_edge("planner", "supervisor_agent")
    graph.add_conditional_edges(
        "supervisor_agent",
        should_execute_next,
        {
            "case_analysis_agent": "case_analysis_agent",
            "statute_retrieval_agent": "statute_retrieval_agent",
            "legal_consult_agent": "legal_consult_agent",
            "verify": "verifier",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "case_analysis_agent",
        should_continue,
        {
            "tools": "case_analysis_tools",
            "limit_exceeded": "tool_limit_exceeded",
            "end": "supervisor_agent",
        },
    )
    graph.add_edge("case_analysis_tools", "collect_case_evidence")
    graph.add_edge("collect_case_evidence", "case_analysis_agent")
    graph.add_conditional_edges(
        "statute_retrieval_agent",
        should_continue,
        {
            "tools": "statute_retrieval_tools",
            "limit_exceeded": "tool_limit_exceeded",
            "end": "supervisor_agent",
        },
    )
    graph.add_edge("statute_retrieval_tools", "collect_statute_evidence")
    graph.add_edge("collect_statute_evidence", "statute_retrieval_agent")
    graph.add_conditional_edges(
        "legal_consult_agent",
        should_continue,
        {
            "tools": "legal_consult_tools",
            "limit_exceeded": "tool_limit_exceeded",
            "end": "supervisor_agent",
        },
    )
    graph.add_edge("legal_consult_tools", "collect_consult_evidence")
    graph.add_edge("collect_consult_evidence", "legal_consult_agent")
    graph.add_edge("tool_limit_exceeded", "supervisor_agent")
    graph.add_conditional_edges(
        "verifier",
        should_after_verifier,
        {
            "replan": "planner",
            "answer_generator": "answer_generator",
        },
    )
    graph.add_edge("answer_generator", END)

    return graph.compile(checkpointer=checkpointer)
