"""Repair Router 局部修复回归用例（§三十 用例 6、§P0-5、§P0-6）。

覆盖四件事：
1. 编造引用只重跑受影响的检索与分析步骤，不触发整体重排；
2. 第一轮已核验的证据与无关步骤的报告必须原样保留（P0-6）；
3. 答案格式类问题只重写答案，不重跑任何 Agent；
4. 修复预算或全请求工具预算用尽后基于已核验证据作答，不再往复。
"""
from __future__ import annotations

from agent.nodes.repair import repair_router_node
from agent.nodes.routing import should_after_repair, should_execute_next
from agent.nodes.supervisor import supervisor_agent_node
from agent.repair import MAX_REPAIR_ROUNDS, plan_repair, repair_targets_for
from agent.state import TaskType, merge_plan_steps
from agent.tool_loop import MAX_TOOL_CALLS_PER_REQUEST

LAW_EVIDENCE = {
    "evidence_id": "law-85",
    "ref_id": "law_001",
    "canonical_law_id": "劳动合同法",
    "law_name": "中华人民共和国劳动合同法(2012修正)",
    "article_no": "第八十五条",
    "source_id": "labor-contract-law-85",
    "source_type": "delilegal_law",
    "validity": "valid",
}
CASE_EVIDENCE = {
    "evidence_id": "case-888",
    "ref_id": "case_001",
    "case_no": "（2023）京02民终888号",
    "case_name": "张某诉某公司劳动争议案",
    "source_type": "delilegal_case",
}


def _report(step_id: str, agent: str) -> dict:
    return {
        "report_id": f"{step_id}:{agent}",
        "task_id": step_id,
        "agent_name": agent,
        "summary": f"{agent} 的第一轮结论",
        "sources": [],
    }


def _state(issue_type: str = "citation_invalid") -> dict:
    """三步计划：事实分析已完成，法条检索与法律分析产出了被质疑的引用。"""
    return {
        "plan": [
            {
                "step_id": "step_1",
                "task_type": TaskType.CASE_ANALYSIS,
                "description": "梳理关键事实",
                "assigned_agent": "case_analysis_agent",
                "required": True,
                "status": "completed",
                "result": _report("step_1", "case_analysis_agent"),
            },
            {
                "step_id": "step_2",
                "task_type": TaskType.STATUTE_RETRIEVAL,
                "description": "检索法条",
                "assigned_agent": "statute_retrieval_agent",
                "required": True,
                "status": "completed",
                "result": _report("step_2", "statute_retrieval_agent"),
            },
            {
                "step_id": "step_3",
                "task_type": TaskType.LEGAL_CONSULTATION,
                "description": "形成法律建议",
                "assigned_agent": "legal_consult_agent",
                "required": True,
                "status": "completed",
                "result": _report("step_3", "legal_consult_agent"),
            },
        ],
        "agent_reports": [
            _report("step_1", "case_analysis_agent"),
            _report("step_2", "statute_retrieval_agent"),
            _report("step_3", "legal_consult_agent"),
        ],
        "retrieved_laws": [dict(LAW_EVIDENCE)],
        "retrieved_cases": [dict(CASE_EVIDENCE)],
        "verified_evidence": {"laws": [dict(LAW_EVIDENCE)], "citations": []},
        "verification_result": {
            "passed": False,
            "needs_retry": True,
            "structured_issues": [
                {
                    "type": issue_type,
                    "severity": "blocking",
                    "source": "deterministic",
                    "agent": "legal_consult_agent",
                    "step_id": "step_3",
                    "message": "引用了无法核验的法条：《中华人民共和国劳动合同法》第九十九条",
                }
            ],
        },
        "repair_count": 0,
    }


def test_fabricated_citation_reopens_only_affected_steps():
    """§三十 用例 6：编造引用 → 局部修复检索与分析，事实分析步骤保持完成。"""
    state = _state()

    result = repair_router_node(state)

    assert result["verification_result"]["repair_targets"] == ["law_retrieval_agent"]
    assert result["supervisor_route"] == "repair"
    assert should_after_repair(result) == "supervisor"
    statuses = {step["step_id"]: step["status"] for step in result["plan"]}
    # 检索步骤及其下游分析一起重开：只补检索不重写分析会让同一条错误引用再次通过。
    assert statuses == {"step_1": "completed", "step_2": "pending", "step_3": "pending"}
    assert [step["step_id"] for step in result["remaining_steps"]] == ["step_2", "step_3"]
    assert [step["step_id"] for step in result["completed_steps"]] == ["step_1"]
    assert result["current_step"] is None
    assert result["retry_count"] == 0
    # 修复指令进入被重开步骤的 description，经 Current Plan 区块传给模型。
    reopened = next(step for step in result["plan"] if step["step_id"] == "step_2")
    assert "只保留本轮检索到的现行有效条文" in reopened["description"]
    assert reopened["result"] is None


def test_repair_preserves_first_round_evidence_and_reports():
    """§P0-6：局部修复不得丢弃第一轮已核验的证据与无关步骤的报告。"""
    state = _state()

    result = repair_router_node(state)

    for key in ("retrieved_laws", "retrieved_cases", "verified_evidence", "agent_reports", "citations"):
        assert key not in result
    assert state["retrieved_laws"] == [dict(LAW_EVIDENCE)]
    assert state["retrieved_cases"] == [dict(CASE_EVIDENCE)]
    assert len(state["agent_reports"]) == 3


def test_missing_case_step_is_synthesized_for_case_repair():
    """计划里没有类案步骤时补建一步，而不是整体重排（§五 按需检索）。"""
    state = _state("case_evidence_insufficient")
    state["plan"] = [state["plan"][0]]
    state["agent_reports"] = [state["agent_reports"][0]]

    repair = plan_repair(state, state["verification_result"])

    assert repair.targets == ["case_retrieval_agent"]
    assert repair.added == ["repair_1"]
    added = repair.plan[-1]
    assert added["step_id"] == "repair_1"
    assert added["assigned_agent"] == "case_retrieval_agent"
    assert added["task_type"] == TaskType.CASE_RETRIEVAL
    assert added["status"] == "pending"
    assert "可核验的案号" in added["description"]


def test_overconfident_conclusion_reruns_only_legal_reasoning():
    """§三十 用例 7：事实不足却给出确定结论 → 只重跑法律推理，检索结果不重做。"""
    state = _state("overconfident")
    state["facts_sufficient"] = False
    state["missing_facts"] = ["是否收到书面解除通知"]

    result = repair_router_node(state)

    assert result["verification_result"]["repair_targets"] == ["legal_reasoning_agent"]
    assert result["supervisor_route"] == "repair"
    statuses = {step["step_id"]: step["status"] for step in result["plan"]}
    # 事实分析与法条检索的第一轮结论保留，只有分析步骤重开（P0-5、P0-6）。
    assert statuses == {"step_1": "completed", "step_2": "completed", "step_3": "pending"}
    assert [step["step_id"] for step in result["remaining_steps"]] == ["step_3"]
    reopened = next(step for step in result["plan"] if step["step_id"] == "step_3")
    assert "证据不足时不得给出确定性结论" in reopened["description"]
    # 证据与已有报告原样保留，不因修复而丢失（P0-6）。
    assert "retrieved_laws" not in result
    assert "agent_reports" not in result


def test_answer_format_issue_only_rewrites_answer():
    state = _state("answer_format_error")

    result = repair_router_node(state)

    assert result["supervisor_route"] == "answer_generator"
    assert should_after_repair(result) == "answer_generator"
    assert "plan" not in result
    assert result["verification_result"]["repair_targets"] == ["answer_generator"]


def test_repair_budget_exhausted_falls_back_to_answering():
    state = _state()
    state["repair_count"] = MAX_REPAIR_ROUNDS + 1

    result = repair_router_node(state)

    assert result["supervisor_route"] == "answer_generator"
    assert "plan" not in result
    assert "修复预算已用尽" in result["supervisor_reason"]


def test_request_tool_budget_exhausted_skips_retrieval_repair():
    """全请求工具预算耗尽时不重开步骤：重跑也检索不到新证据，只会白花一轮调用。"""
    state = _state()
    state["tool_call_total"] = MAX_TOOL_CALLS_PER_REQUEST

    result = repair_router_node(state)

    assert result["supervisor_route"] == "answer_generator"
    assert should_after_repair(result) == "answer_generator"
    assert "plan" not in result
    assert "全请求工具预算已耗尽" in result["supervisor_reason"]
    # 修复目标仍然记录在核验结果里，便于观测「本该修但预算不够」的分布。
    assert result["verification_result"]["repair_targets"] == ["law_retrieval_agent"]


def test_request_tool_budget_does_not_block_answer_only_repair():
    """答案格式类修复不调工具，工具预算耗尽不该拦住它。"""
    state = _state("answer_format_error")
    state["tool_call_total"] = MAX_TOOL_CALLS_PER_REQUEST

    result = repair_router_node(state)

    assert result["supervisor_route"] == "answer_generator"
    assert "仅需重写答案" in result["supervisor_reason"]


def test_warning_issues_do_not_trigger_repair():
    """warning 只作风险提示：不路由、不重跑，直接基于已核验证据作答。"""
    state = _state()
    state["verification_result"]["structured_issues"] = [
        {
            "type": "obsolete_law_risk",
            "severity": "warning",
            "source": "semantic",
            "message": "法规有效性存在风险",
        }
    ]

    assert repair_targets_for(state["verification_result"]["structured_issues"]) == []
    result = repair_router_node(state)

    assert result["supervisor_route"] == "answer_generator"
    assert "无对应修复目标" in result["supervisor_reason"]


async def test_supervisor_dispatches_reopened_step_after_repair():
    """修复后由 Supervisor 继续调度：先重跑法条检索，事实分析不再执行。"""
    state = _state()
    repair = repair_router_node(state)
    # 图中 plan 走 merge_plan_steps reducer，这里按同一口径合并回状态。
    state["plan"] = merge_plan_steps(state["plan"], repair["plan"])
    state.update({key: value for key, value in repair.items() if key != "plan"})

    dispatched = await supervisor_agent_node(state)

    assert dispatched["current_step"] == "step_2"
    assert dispatched["supervisor_route"] == "statute_retrieval_agent"
    assert should_execute_next(dispatched) == "statute_retrieval_agent"
