"""Regression tests for the explicit Result Verifier stage."""
from __future__ import annotations

import json

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
    statute_report = {
        "report_id": "step_1:statute_retrieval_agent",
        "task_id": "step_1",
        "agent_name": "statute_retrieval_agent",
        "summary": "已检索适用法规",
        "findings": {"evidence_insufficient": False},
        "sources": [LAW],
    }
    case_report = {
        "report_id": "step_2:case_analysis_agent",
        "task_id": "step_2",
        "agent_name": "case_analysis_agent",
        "summary": "已检索类案",
        "findings": {"cases": [CASE["case_no"]]},
        "sources": [CASE],
    }
    consult_report = {
        "report_id": "step_3:legal_consult_agent",
        "task_id": "step_3",
        "agent_name": "legal_consult_agent",
        "summary": "可要求对方承担违约责任",
        "findings": {
            "analysis": "根据《民法典》第五百七十七条，可以结合（2024）京01民终100号评估损失。"
        },
        "sources": [LAW, CASE],
    }
    return {
        "messages": [HumanMessage(content="对方违约怎么办？")],
        "plan": [
            _step("step_1", TaskType.STATUTE_RETRIEVAL, "statute_retrieval_agent", statute_report),
            _step("step_2", TaskType.CASE_RETRIEVAL, "case_analysis_agent", case_report),
            _step("step_3", TaskType.LEGAL_CONSULTATION, "legal_consult_agent", consult_report),
        ],
        "agent_reports": [statute_report, case_report, consult_report],
        "retrieved_laws": [LAW],
        "retrieved_cases": [CASE],
        "verifier_retry_count": 0,
    }


def test_deterministic_verifier_passes_fully_grounded_results():
    result = verify_plan_results(_grounded_state())

    assert result == {
        "passed": True,
        "score": 1.0,
        "issues": [],
        "missing_sources": [],
        "invalid_citations": [],
        "needs_retry": False,
        "retry_reason": None,
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
    assert any("不存在的法条" in item for item in result["invalid_citations"])
    assert any("不存在的案例" in item for item in result["invalid_citations"])
    assert any("不存在的案号" in item for item in result["invalid_citations"])


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
    assert any("失效风险" in item for item in result["issues"])


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


async def test_result_verifier_first_failure_resets_execution_without_answer(monkeypatch):
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: _EmptyVerifierLLM())
    state = _grounded_state()
    state["agent_reports"][-1]["findings"] = {"analysis": "根据《民法典》第九百九十九条，可以胜诉。"}

    result = await result_verifier_node(state)

    assert result["verification_result"]["needs_retry"] is True
    assert result["replan_retry_count"] == 1
    assert result["verifier_retry_count"] == 1
    assert result["supervisor_route"] == "replan"
    assert all(step["status"] == "pending" for step in result["plan"])
    assert result["agent_reports"] == []
    assert "messages" not in result
    assert should_after_verifier(result) == "replan"


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
    def __init__(self):
        self.messages = []

    def bind_tools(self, *_args, **_kwargs):
        raise AssertionError("Answer Generator 不得绑定或调用工具")

    async def ainvoke(self, messages):
        self.messages = messages
        return AIMessage(content=(
            "1. 结论\n你一定胜诉。\n"
            "2. 法律分析\n对方可能承担违约责任。\n"
            "3. 法律依据\n根据《民法典》第五百七十七条及《民法典》第九百九十九条处理。\n"
            "4. 类案参考\n参考（2024）京01民终100号和（2025）沪01民终999号。\n"
            "5. 风险与不确定性\n以核验结果为准。\n"
            "6. 建议下一步\n按现有报告建议处理。"
        ))


async def test_answer_generator_adds_risk_notice_and_removes_untrusted_citation(monkeypatch):
    llm = _AnswerLLM()
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: llm)
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

    result = await answer_generator_node(state)
    content = result["messages"][0].content

    assert content.startswith("风险提示：")
    assert "第九百九十九条" not in content
    assert "（2025）沪01民终999号" not in content
    assert "一定胜诉" not in content
    assert "《民法典》第五百七十七条（来源：delilegal_law，law-1）" in content
    assert "（2024）京01民终100号（来源：delilegal_case，case-1）" in content
    payload = json.loads(llm.messages[1].content)
    assert payload["原始问题"] == state["original_query"]
    assert payload["专家报告"] == state["agent_reports"]
    assert payload["检索法条"] == state["retrieved_laws"]
    assert payload["检索案例"] == state["retrieved_cases"]
    assert payload["核验结果"] == state["verification_result"]
    assert [item["source_id"] for item in result["citations"]] == ["law-1", "case-1"]
    assert result["supervisor_finalized"] is True
