"""Regression tests for the declarative main graph topology."""
from agent.graph import build_graph
from agent.nodes.query import query_rewrite_node


def test_query_rewrite_preserves_original_and_normalizes_whitespace():
    from langchain_core.messages import HumanMessage

    result = query_rewrite_node({"messages": [HumanMessage(content="  劳动合同\n 到期  ")]})

    assert result == {
        "original_query": "劳动合同\n 到期",
        "rewritten_query": "劳动合同 到期",
    }


def test_main_graph_exposes_the_final_pipeline_nodes_and_edges():
    drawable = build_graph(checkpointer=None).get_graph()
    nodes = set(drawable.nodes)
    edges = {(edge.source, edge.target) for edge in drawable.edges}

    assert {
        "context_compaction",
        "memory",
        "inject_doc",
        "query_rewrite",
        "fact_merge",
        "intent_router",
        "fact_analysis",
        "clarification",
        "complexity_router",
        "planner",
        "supervisor",
        "case_analysis_agent",
        "statute_retrieval_agent",
        # §五：类案检索是独立执行单元，不再挂在事实分析 Agent 上。
        "case_retrieval_agent",
        "legal_consult_agent",
        "result_verifier",
        "answer_generator",
    } <= nodes
    assert {
        ("__start__", "context_compaction"),
        ("context_compaction", "memory"),
        ("memory", "inject_doc"),
        ("inject_doc", "query_rewrite"),
        # Fact Merge 先跑，Intent Router 才能看到「原始问题 + 用户补充」（§八）。
        ("query_rewrite", "fact_merge"),
        ("fact_merge", "intent_router"),
        ("intent_router", "fact_analysis"),
        ("fact_analysis", "complexity_router"),
        ("fact_analysis", "clarification"),
        ("clarification", "__end__"),
        # §P1-1：简单问题从 Complexity Router 直达 Supervisor，不经过 Planner。
        ("complexity_router", "planner"),
        ("complexity_router", "supervisor"),
        ("planner", "supervisor"),
        ("supervisor", "result_verifier"),
        ("result_verifier", "answer_generator"),
        ("answer_generator", "__end__"),
    } <= edges


def test_specialists_return_to_supervisor_after_their_tool_loops():
    drawable = build_graph(checkpointer=None).get_graph()
    edges = {(edge.source, edge.target) for edge in drawable.edges}

    for agent_name, tools_name, collector_name in (
        ("case_analysis_agent", "case_analysis_tools", "collect_case_evidence"),
        ("statute_retrieval_agent", "statute_retrieval_tools", "collect_statute_evidence"),
        ("case_retrieval_agent", "case_retrieval_tools", "collect_case_retrieval_evidence"),
        ("legal_consult_agent", "legal_consult_tools", "collect_consult_evidence"),
    ):
        assert ("supervisor", agent_name) in edges
        assert (agent_name, tools_name) in edges
        assert (tools_name, collector_name) in edges
        assert (collector_name, agent_name) in edges
        assert (agent_name, "supervisor") in edges
