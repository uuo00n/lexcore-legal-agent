"""AgentState reducer tests."""
from __future__ import annotations

from agent.state import (
    merge_agent_reports,
    merge_plan_steps,
    merge_retrieved_laws,
    merge_unique_items,
)


def test_plan_reducer_updates_one_step_without_losing_other_steps():
    original = [
        {"step_id": "step_1", "status": "running", "description": "分析案件"},
        {"step_id": "step_2", "status": "pending", "description": "检索法规"},
    ]

    merged = merge_plan_steps(original, [{
        "step_id": "step_1",
        "status": "completed",
        "result": {"summary": "事实已整理"},
    }])

    assert merged[0]["description"] == "分析案件"
    assert merged[0]["status"] == "completed"
    assert merged[1] == original[1]


def test_evidence_and_report_reducers_deduplicate_stable_ids(grounded_law):
    duplicate = {**grounded_law, "content": "重复版本"}
    laws = merge_retrieved_laws([grounded_law], [duplicate])
    reports = merge_agent_reports(
        [{"report_id": "report-1", "summary": "初始"}],
        [{"report_id": "report-1", "summary": "重复"}],
    )

    assert laws == [grounded_law]
    assert reports == [{"report_id": "report-1", "summary": "初始"}]


def test_explicit_empty_list_clears_round_scoped_state(grounded_law):
    assert merge_unique_items([grounded_law], []) == []
    assert merge_plan_steps([{"step_id": "step_1", "status": "completed"}], []) == []
