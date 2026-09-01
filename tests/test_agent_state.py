"""AgentState 数据结构与 reducer 的回归测试。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agent.state import AgentState, merge_agent_reports, merge_plan_steps


def test_merge_plan_steps_updates_by_step_id_without_losing_other_steps() -> None:
    original = [
        {
            "step_id": "step_1",
            "task_type": "statute_retrieval",
            "description": "检索法条",
            "status": "pending",
            "assigned_agent": "statute_retrieval_agent",
            "result": None,
        },
        {
            "step_id": "step_2",
            "task_type": "case_retrieval",
            "description": "检索案例",
            "status": "pending",
            "assigned_agent": "case_retrieval_agent",
            "result": None,
        },
    ]

    merged = merge_plan_steps(
        original,
        [{"step_id": "step_1", "status": "completed", "result": {"count": 2}}],
    )

    assert merged[0]["status"] == "completed"
    assert merged[0]["description"] == "检索法条"
    assert merged[1] == original[1]


def test_agent_report_reducer_merges_current_and_legacy_reports_with_explicit_clear() -> None:
    reports = merge_agent_reports(
        [{"agent_name": "case_analysis_agent", "status": "completed"}],
        [{"agent": "legal_consult_agent", "status": "completed"}],
    )

    assert [report.get("agent_name") or report.get("agent") for report in reports] == [
        "case_analysis_agent",
        "legal_consult_agent",
    ]
    assert merge_agent_reports(reports, []) == []


def test_parallel_retrieval_updates_are_merged_in_state_graph() -> None:
    """两个并行节点写同一列表时，两个结果都应保留。"""

    graph = StateGraph(AgentState)
    graph.add_node(
        "statute_source_a",
        lambda _state: {
            "retrieved_laws": [
                {
                    "law_name": "中华人民共和国民法典",
                    "article_no": "第五百七十七条",
                    "content": "违约责任",
                    "source_type": "source_a",
                }
            ]
        },
    )
    graph.add_node(
        "statute_source_b",
        lambda _state: {
            "retrieved_laws": [
                {
                    "law_name": "中华人民共和国民法典",
                    "article_no": "第五百八十五条",
                    "content": "违约金",
                    "source_type": "source_b",
                }
            ]
        },
    )
    graph.add_edge(START, "statute_source_a")
    graph.add_edge(START, "statute_source_b")
    graph.add_edge("statute_source_a", END)
    graph.add_edge("statute_source_b", END)

    result = graph.compile().invoke({"retrieved_laws": []})

    assert {law["article_no"] for law in result["retrieved_laws"]} == {
        "第五百七十七条",
        "第五百八十五条",
    }
