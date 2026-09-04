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


def test_evidence_and_report_reducers_replace_duplicates_by_stable_id(grounded_law):
    """同一条证据／同一 ``report_id`` 只保留一条，且由最新写入覆盖。

    局部修复（Repair Router）会让同一个 Agent 就同一 task_id 重新提交报告，
    若旧报告胜出，修复结果就进不了核验（P0-6）。
    """
    duplicate = {**grounded_law, "content": "重复版本"}
    laws = merge_retrieved_laws([grounded_law], [duplicate])
    reports = merge_agent_reports(
        [{"report_id": "report-1", "summary": "初始"}],
        [{"report_id": "report-1", "summary": "修复后"}],
    )

    assert laws == [duplicate]
    assert reports == [{"report_id": "report-1", "summary": "修复后"}]


def test_explicit_empty_list_clears_round_scoped_state(grounded_law):
    assert merge_unique_items([grounded_law], []) == []
    assert merge_plan_steps([{"step_id": "step_1", "status": "completed"}], []) == []
