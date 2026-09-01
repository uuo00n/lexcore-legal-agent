"""Declarative registration and compilation of the main LangGraph topology."""
from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agent.graph_runtime import observed_node, observed_tool_node, resolve_supervisor_nodes
from agent.nodes import (
    answer_generator_node,
    case_analysis_agent_node,
    collect_retrieved_laws,
    context_compaction_node,
    inject_doc_node,
    intent_router_node,
    legal_consult_agent_node,
    memory_node,
    planner_node,
    query_rewrite_node,
    result_verifier_node,
    should_after_verifier,
    should_continue,
    should_execute_next,
    statute_retrieval_agent_node,
    supervisor_agent_node,
    tool_limit_observation_node,
)
from agent.state import AgentState
from agent.tools import CASE_ANALYSIS_TOOLS, LEGAL_CONSULT_TOOLS, STATUTE_RETRIEVAL_TOOLS

# Compatibility import for existing observability integrations.
_observed_node = observed_node
_DEFAULT_SUPERVISOR_NODE = supervisor_agent_node


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> Any:
    """Register the main workflow and compile it."""
    graph = StateGraph(AgentState)
    router_node, supervisor_node = resolve_supervisor_nodes(
        intent_router_node,
        supervisor_agent_node,
        _DEFAULT_SUPERVISOR_NODE,
    )

    graph.add_node(
        "context_compaction",
        observed_node("context_compaction", context_compaction_node),
    )
    graph.add_node("memory", observed_node("memory", memory_node))
    graph.add_node("inject_doc", observed_node("inject_doc", inject_doc_node))
    graph.add_node("query_rewrite", observed_node("query_rewrite", query_rewrite_node))
    graph.add_node("intent_router", observed_node("intent_router", router_node))
    graph.add_node("planner", observed_node("planner", planner_node))
    graph.add_node(
        "supervisor",
        observed_node("supervisor", supervisor_node, agent_name="supervisor_agent"),
    )
    graph.add_node(
        "case_analysis_agent",
        observed_node(
            "case_analysis_agent",
            case_analysis_agent_node,
            agent_name="case_analysis_agent",
        ),
    )
    graph.add_node(
        "statute_retrieval_agent",
        observed_node(
            "statute_retrieval_agent",
            statute_retrieval_agent_node,
            agent_name="statute_retrieval_agent",
        ),
    )
    graph.add_node(
        "legal_consult_agent",
        observed_node(
            "legal_consult_agent",
            legal_consult_agent_node,
            agent_name="legal_consult_agent",
        ),
    )
    graph.add_node(
        "result_verifier",
        observed_node("result_verifier", result_verifier_node),
    )
    graph.add_node(
        "answer_generator",
        observed_node("answer_generator", answer_generator_node),
    )
    graph.add_node(
        "case_analysis_tools",
        observed_tool_node(
            "case_analysis_tools",
            CASE_ANALYSIS_TOOLS,
            agent_name="case_analysis_agent",
        ),
    )
    graph.add_node(
        "statute_retrieval_tools",
        observed_tool_node(
            "statute_retrieval_tools",
            STATUTE_RETRIEVAL_TOOLS,
            agent_name="statute_retrieval_agent",
        ),
    )
    graph.add_node(
        "legal_consult_tools",
        observed_tool_node(
            "legal_consult_tools",
            LEGAL_CONSULT_TOOLS,
            agent_name="legal_consult_agent",
        ),
    )
    graph.add_node(
        "tool_limit_exceeded",
        observed_node("tool_limit_exceeded", tool_limit_observation_node),
    )
    graph.add_node(
        "collect_case_evidence",
        observed_node(
            "collect_case_evidence",
            collect_retrieved_laws,
            agent_name="case_analysis_agent",
        ),
    )
    graph.add_node(
        "collect_statute_evidence",
        observed_node(
            "collect_statute_evidence",
            collect_retrieved_laws,
            agent_name="statute_retrieval_agent",
        ),
    )
    graph.add_node(
        "collect_consult_evidence",
        observed_node(
            "collect_consult_evidence",
            collect_retrieved_laws,
            agent_name="legal_consult_agent",
        ),
    )

    graph.add_edge(START, "context_compaction")
    graph.add_edge("context_compaction", "memory")
    graph.add_edge("memory", "inject_doc")
    graph.add_edge("inject_doc", "query_rewrite")
    graph.add_edge("query_rewrite", "intent_router")
    graph.add_edge("intent_router", "planner")
    graph.add_edge("planner", "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        should_execute_next,
        {
            "case_analysis_agent": "case_analysis_agent",
            "statute_retrieval_agent": "statute_retrieval_agent",
            "legal_consult_agent": "legal_consult_agent",
            "verify": "result_verifier",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "case_analysis_agent",
        should_continue,
        {
            "tools": "case_analysis_tools",
            "limit_exceeded": "tool_limit_exceeded",
            "end": "supervisor",
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
            "end": "supervisor",
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
            "end": "supervisor",
        },
    )
    graph.add_edge("legal_consult_tools", "collect_consult_evidence")
    graph.add_edge("collect_consult_evidence", "legal_consult_agent")
    graph.add_edge("tool_limit_exceeded", "supervisor")
    graph.add_conditional_edges(
        "result_verifier",
        should_after_verifier,
        {"replan": "planner", "answer_generator": "answer_generator"},
    )
    graph.add_edge("answer_generator", END)

    return graph.compile(checkpointer=checkpointer)
