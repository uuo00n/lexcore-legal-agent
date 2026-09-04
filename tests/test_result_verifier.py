"""Regression tests for the explicit Result Verifier stage."""
from __future__ import annotations

import json
from copy import deepcopy

from langchain_core.messages import AIMessage, HumanMessage

from agent.nodes.answer import answer_generator_node
from agent.nodes.routing import should_after_verifier
from agent.nodes.verifier import result_verifier_node, verify_plan_results
from agent.state import TaskType


LAW = {
    "law_name": "中华人民共和国民法典",
    "article_no": "第五百七十七条",
    "content": "当事人一方不履行合同义务，应当承担违约责任。",
    "source_type": "delilegal_law",
    "source_id": "law-1",
    "timeliness_name": "现行有效",
}
CASE = {
    "case_id": "case-1",
    "case_name": "甲公司诉乙公司合同纠纷案",
    "case_no": "（2024）京01民终100号",
    "summary": "法院支持有证据证明的违约损失。",
    "source_type": "delilegal_case",
    "source_id": "case-1",
}


def _step(step_id, task_type, agent_name, report):
    return {
        "step_id": step_id,
        "task_type": task_type,
        "description": "执行必须任务",
        "assigned_agent": agent_name,
        "required": True,
        "status": "completed",
        "result": report,
    }


def _grounded_state():
    # 每个用例都拿到独立副本，避免某个用例把法条改成「已废止」后污染其他用例。
    law = deepcopy(LAW)
    case = deepcopy(CASE)
    statute_report = {
        "report_id": "step_1:statute_retrieval_agent",
        "task_id": "step_1",
        "agent_name": "statute_retrieval_agent",
        "summary": "已检索适用法规",
        "findings": {"evidence_insufficient": False},
        "sources": [law],
    }
    case_report = {
        "report_id": "step_2:case_analysis_agent",
        "task_id": "step_2",
        "agent_name": "case_analysis_agent",
        "summary": "已检索类案",
        "findings": {"cases": [case["case_no"]]},
        "sources": [case],
    }
    consult_report = {
        "report_id": "step_3:legal_consult_agent",
        "task_id": "step_3",
        "agent_name": "legal_consult_agent",
        "summary": "可要求对方承担违约责任",
        "findings": {
            "analysis": "根据《民法典》第五百七十七条，可以结合（2024）京01民终100号评估损失。"
        },
        "sources": [law, case],
    }
    return {
        "messages": [HumanMessage(content="对方违约怎么办？")],
        "plan": [
            _step("step_1", TaskType.STATUTE_RETRIEVAL, "statute_retrieval_agent", statute_report),
            _step("step_2", TaskType.CASE_RETRIEVAL, "case_analysis_agent", case_report),
            _step("step_3", TaskType.LEGAL_CONSULTATION, "legal_consult_agent", consult_report),
        ],
        "agent_reports": [statute_report, case_report, consult_report],
        "retrieved_laws": [law],
        "retrieved_cases": [case],
        "verifier_retry_count": 0,
    }


def test_deterministic_verifier_passes_fully_grounded_results():
    result = verify_plan_results(_grounded_state())

    # 别名写法《民法典》第五百七十七条与证据《中华人民共和国民法典》归一到同一条，
    # 因此三条引用全部核验通过，且不产生任何结构化问题（P0-1、P0-2）。
    assert result == {
        "passed": True,
        "score": 1.0,
        "issues": [],
        "missing_sources": [],
        "invalid_citations": [],
        "needs_retry": False,
        "retry_reason": None,
        "structured_issues": [],
        "citation_report": {
            "citation_total": 3,
            "citation_verified": 3,
            "citation_unsupported": 0,
        },
        "repair_targets": [],
        # 确定性核验单独运行时不涉及语义半边，因此没有降级（§P1-6）。
        "verification_degraded": False,
    }


def test_deterministic_verifier_rejects_hallucinated_law_and_case():
    state = _grounded_state()
    report = state["agent_reports"][-1]
    report["findings"] = {
        "analysis": "根据《民法典》第九百九十九条，并参考（2025）沪01民终999号，可以胜诉。"
    }
    report["sources"] = [
        {**LAW, "article_no": "第九百九十九条"},
        {**CASE, "case_id": "case-x", "source_id": "case-x", "case_no": "（2025）沪01民终999号"},
    ]

    result = verify_plan_results(state)

    assert result["passed"] is False
    assert result["needs_retry"] is True
    # 本轮检索到了民法典，但没有第九百九十九条；案号真实存在案名却是编造的组合。
    assert any(
        "《中华人民共和国民法典》第九百九十九条" in item for item in result["invalid_citations"]
    )
    assert any("未检索到被引用的条文" in item for item in result["invalid_citations"])
    assert any("（2025）沪01民终999号" in item for item in result["invalid_citations"])
    assert any("不存在该案号" in item for item in result["invalid_citations"])
    assert "citation_invalid" in {
        issue["type"] for issue in result["structured_issues"]
    }
    assert all(
        issue["source"] == "deterministic" for issue in result["structured_issues"]
    )
    assert result["citation_report"] == {
        "citation_total": 5,
        "citation_verified": 2,
        "citation_unsupported": 3,
    }


def test_deterministic_verifier_flags_missing_source_conflict_and_obsolete_law():
    state = _grounded_state()
    state["retrieved_laws"][0]["timeliness_name"] = "已废止"
    fact_report = {
        "report_id": "step_0:case_analysis_agent",
        "task_id": "step_0",
        "agent_name": "case_analysis_agent",
        "status": "needs_more_facts",
        "summary": "关键事实不足",
        "findings": {},
        "sources": [],
    }
    state["agent_reports"].insert(0, fact_report)
    consult_report = state["agent_reports"][-1]
    consult_report["sources"] = []

    result = verify_plan_results(state)

    assert result["passed"] is False
    assert any("关键法律结论没有可信 source" in item for item in result["missing_sources"])
    assert any("关键事实不足" in item for item in result["issues"])
    assert any("已失效或废止" in item for item in result["invalid_citations"])


class _EmptyVerifierLLM:
    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        return {
            "severe_conflicts": [],
            "unsupported_conclusions": [],
            "obsolete_law_risks": [],
            "missing_sources": [],
        }


async def test_result_verifier_first_failure_repairs_locally_without_answer(monkeypatch):
    """§P0-5：编造引用只触发局部修复，不再重置整个计划、不清空第一轮成果（P0-6）。"""
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: _EmptyVerifierLLM())
    state = _grounded_state()
    state["agent_reports"][-1]["findings"] = {"analysis": "根据《民法典》第九百九十九条，可以胜诉。"}
    laws_before = [dict(item) for item in state["retrieved_laws"]]
    reports_before = len(state["agent_reports"])

    result = await result_verifier_node(state)

    assert result["verification_result"]["needs_retry"] is True
    assert "law_retrieval_agent" in result["verification_result"]["repair_targets"]
    assert result["supervisor_route"] == "repair"
    assert result["repair_count"] == 1
    assert "replan_retry_count" not in result
    assert "verifier_retry_count" not in result
    assert "plan" not in result
    assert "agent_reports" not in result
    assert "messages" not in result
    assert state["retrieved_laws"] == laws_before
    assert len(state["agent_reports"]) == reports_before
    assert should_after_verifier(result) == "repair"


async def test_result_verifier_second_failure_cannot_loop(monkeypatch):
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: _EmptyVerifierLLM())
    state = _grounded_state()
    state["verifier_retry_count"] = 1
    state["plan"][0]["status"] = "failed"

    result = await result_verifier_node(state)

    assert result["verification_result"]["passed"] is False
    assert result["verification_result"]["needs_retry"] is False
    assert result["verification_result"]["retry_reason"] is None
    assert "verifier_retry_count" not in result
    assert should_after_verifier(result) == "answer_generator"
    assert should_after_verifier({
        "verification_result": {"needs_retry": True},
        "verifier_retry_count": 2,
    }) == "answer_generator"


class _AnswerLLM:
    """始终返回同一份带未核验引用的草稿：两次生成都不通过审计。"""

    def __init__(self, content: str | None = None):
        self.messages = []
        self.payloads: list[dict] = []
        self._content = content or (
            "1. 结论\n你一定胜诉。\n"
            "2. 法律分析\n对方可能承担违约责任。\n"
            "3. 法律依据\n根据《民法典》第五百七十七条及《民法典》第九百九十九条处理。\n"
            "4. 类案参考\n参考（2024）京01民终100号和（2025）沪01民终999号。\n"
            "5. 风险与不确定性\n以核验结果为准。\n"
            "6. 建议下一步\n按现有报告建议处理。"
        )

    def bind_tools(self, *_args, **_kwargs):
        raise AssertionError("Answer Generator 不得绑定或调用工具")

    async def ainvoke(self, messages):
        self.messages = messages
        self.payloads.append(json.loads(messages[1].content))
        return AIMessage(content=self._content)


def _uncertain_state():
    state = _grounded_state()
    state["original_query"] = "原始问题：对方拒绝履约怎么办？"
    state["verification_result"] = {
        "passed": False,
        "score": 0.5,
        "issues": ["第二次核验仍缺少依据"],
        "missing_sources": ["关键结论缺少 source"],
        "invalid_citations": [],
        "needs_retry": False,
        "retry_reason": None,
    }
    return state


async def test_answer_generator_regenerates_then_rebuilds_when_draft_is_ungrounded(monkeypatch):
    """§P2：草稿带未核验引用时重新生成一次，仍不通过就用已核验证据确定性重建。"""
    llm = _AnswerLLM()
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: llm)
    state = _uncertain_state()

    result = await answer_generator_node(state)
    content = result["messages"][0].content

    assert content.startswith("风险提示：")
    # 未核验的法条与案号不会出现，而且不留任何「已移除」替换标记（§二 问题 10）。
    assert "第九百九十九条" not in content
    assert "（2025）沪01民终999号" not in content
    assert "已移除" not in content
    assert "一定胜诉" not in content
    assert "《民法典》第五百七十七条（来源：delilegal_law，law-1）" in content
    assert "（2024）京01民终100号（来源：delilegal_case，case-1）" in content

    # 只重新生成一次，不无限重试。
    assert len(llm.payloads) == 2
    first, second = llm.payloads
    assert first["原始问题"] == state["original_query"]
    assert first["检索法条"] == state["retrieved_laws"]
    assert first["检索案例"] == state["retrieved_cases"]
    # §P2：报告的内部标识不进 prompt，模型抄不到内部 Agent 名与 step_id。
    assert [sorted(item) for item in first["专家报告"]] == [
        ["findings", "sources", "summary"],
        ["findings", "sources", "summary"],
        ["findings", "sources", "summary"],
    ]
    assert "legal_consult_agent" not in json.dumps(first, ensure_ascii=False)
    # §P2：核验内部信息（问题原文、缺失来源）不得进入 prompt，只给脱敏视图。
    assert first["核验结果"] == {
        "是否通过": False,
        "风险提示": ["本轮核验发现结论仍存在不确定性，需要结合完整事实与现行有效法律文本再判断。"],
        "引用统计": {},
    }
    assert "第二次核验仍缺少依据" not in json.dumps(first, ensure_ascii=False)
    assert "允许引用" not in first
    # 第二次生成带上允许引用清单，而不是让模型自行修补上一稿。
    assert second["允许引用"] == [
        "《中华人民共和国民法典》第五百七十七条",
        "（2024）京01民终100号",
    ]

    assert [item["source_id"] for item in result["citations"]] == ["law-1", "case-1"]
    assert result["supervisor_finalized"] is True
    # §P2：answer_score 由本节点算一次并写进 State，供 API 层直接复用。
    assert set(result["answer_score"]) == {"score", "checks", "citations"}
    assert result["answer_score"]["citations"]["is_fully_supported"] is True


async def test_answer_generator_keeps_grounded_draft_without_regenerating(monkeypatch):
    """引用全部命中已核验证据时，草稿原样保留，不触发重生成也不重建。"""
    llm = _AnswerLLM(
        "1. 结论\n可以要求对方承担违约责任。\n"
        "2. 法律分析\n根据《民法典》第五百七十七条，对方应当承担违约责任。\n"
        "3. 法律依据\n《民法典》第五百七十七条。\n"
        "4. 类案参考\n（2024）京01民终100号。\n"
        "5. 风险与不确定性\n仍需结合完整证据判断。\n"
        "6. 建议下一步\n先固定履约与付款凭证。"
    )
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: llm)

    result = await answer_generator_node(_grounded_state())
    content = result["messages"][0].content

    assert len(llm.payloads) == 1
    assert content.startswith("1. 结论")
    assert "可以要求对方承担违约责任。" in content
