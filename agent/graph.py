"""LangGraph StateGraph 构建 —— 定义 ReAct 循环的图拓扑。"""
from __future__ import annotations

from typing import Any

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

    graph.add_node("context_compaction", context_compaction_node)
    graph.add_node("memory", memory_node)
    graph.add_node("inject_doc", inject_doc_node)
    graph.add_node("request_router", supervisor_agent_node)
    graph.add_node("supervisor_agent", supervisor_agent_node)
    graph.add_node("planner", planner_node)
    graph.add_node("case_analysis_agent", case_analysis_agent_node)
    graph.add_node("statute_retrieval_agent", statute_retrieval_agent_node)
    graph.add_node("legal_consult_agent", legal_consult_agent_node)
    graph.add_node("verifier", result_verifier_node)
    graph.add_node("answer_generator", answer_generator_node)
    graph.add_node(
        "case_analysis_tools",
        ToolNode(CASE_ANALYSIS_TOOLS, handle_tool_errors=tool_error_observation),
    )
    graph.add_node(
        "statute_retrieval_tools",
        ToolNode(STATUTE_RETRIEVAL_TOOLS, handle_tool_errors=tool_error_observation),
    )
    graph.add_node(
        "legal_consult_tools",
        ToolNode(LEGAL_CONSULT_TOOLS, handle_tool_errors=tool_error_observation),
    )
    graph.add_node("tool_limit_exceeded", tool_limit_observation_node)
    graph.add_node("collect_case_evidence", collect_retrieved_laws)
    graph.add_node("collect_statute_evidence", collect_retrieved_laws)
    graph.add_node("collect_consult_evidence", collect_retrieved_laws)

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
            "retry": "supervisor_agent",
            "answer_generator": "answer_generator",
        },
    )
    graph.add_edge("answer_generator", END)

    return graph.compile(checkpointer=checkpointer)
