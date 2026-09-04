"""Result Verifier 失败后的局部修复与有界重排测试（§P0-5、P0-6）。"""
from __future__ import annotations

from langchain_core.messages import HumanMessage

from agent.nodes.routing import should_after_verifier
from agent.nodes.verifier import result_verifier_node, verify_plan_results
from agent.state import TaskType


class EmptyVerifierLLM:
    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        return {
            "severe_conflicts": [],
            "unsupported_conclusions": [],
            "obsolete_law_risks": [],
            "missing_sources": [],
        }


def _failed_state(law: dict) -> dict:
    report = {
        "report_id": "step_1:legal_consult_agent",
        "task_id": "step_1",
        "agent_name": "legal_consult_agent",
        "summary": "可以要求经济补偿",
        "findings": {
            "analysis": "根据《中华人民共和国劳动合同法》第九十九条，应当支付赔偿。"
        },
        "sources": [{**law, "article_no": "第九十九条"}],
    }
    return {
        "messages": [HumanMessage(content="公司无故解除劳动合同怎么办？")],
        "plan": [{
            "step_id": "step_1",
            "task_type": TaskType.LEGAL_CONSULTATION,
            "description": "形成法律建议",
            "assigned_agent": "legal_consult_agent",
            "required": True,
            "status": "completed",
            "result": report,
        }],
        "agent_reports": [report],
        "retrieved_laws": [law],
        "retrieved_cases": [],
        "replan_retry_count": 0,
    }


def test_verifier_fails_hallucinated_citation(grounded_law):
    result = verify_plan_results(_failed_state(grounded_law))

    assert result["passed"] is False
    assert result["needs_retry"] is True
    assert result["invalid_citations"]
    assert "第九十九条" in "".join(result["invalid_citations"])


async def test_first_verifier_failure_routes_to_local_repair(monkeypatch, grounded_law):
    """§P0-5：引用类失败改为局部修复——Verifier 不再重置计划、不清空报告与证据（P0-6）。"""
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: EmptyVerifierLLM())
    state = _failed_state(grounded_law)

    result = await result_verifier_node(state)

    assert result["verification_result"]["passed"] is False
    assert result["verification_result"]["needs_retry"] is True
    assert result["verification_result"]["repair_targets"] == [
        "law_retrieval_agent",
        "legal_reasoning_agent",
    ]
    assert result["supervisor_route"] == "repair"
    assert result["repair_count"] == 1
    assert should_after_verifier(result) == "repair"
    # 重开步骤是 Repair Router 的职责；Verifier 自己不得改写计划或丢弃第一轮成果。
    assert "plan" not in result
    assert "agent_reports" not in result
    assert "replan_retry_count" not in result
    assert state["retrieved_laws"] == [grounded_law]


async def test_second_verifier_failure_stops_repair_loop(monkeypatch, grounded_law):
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: EmptyVerifierLLM())
    state = _failed_state(grounded_law)
    state["repair_count"] = 1

    result = await result_verifier_node(state)

    assert result["verification_result"]["passed"] is False
    assert result["verification_result"]["needs_retry"] is False
    assert result["supervisor_route"] == "answer_generator"
    assert should_after_verifier(result) == "answer_generator"


async def test_unroutable_failure_still_uses_bounded_replan(monkeypatch, grounded_law):
    """计划本身不可修复（``plan_incomplete``）时保留原有的单次整体重排。"""
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: EmptyVerifierLLM())
    state = _failed_state(grounded_law)
    state["plan"] = [{
        "step_id": "step_1",
        "task_type": TaskType.LEGAL_CONSULTATION,
        "description": "形成法律建议",
        "assigned_agent": "legal_consult_agent",
        "required": True,
        "status": "failed",
        "result": {"error": "specialist_did_not_return_report"},
    }]
    state["agent_reports"] = []

    result = await result_verifier_node(state)

    assert result["verification_result"]["repair_targets"] == []
    assert "plan_incomplete" in {
        issue["type"] for issue in result["verification_result"]["structured_issues"]
    }
    assert result["supervisor_route"] == "replan"
    assert result["replan_retry_count"] == 1
    assert result["plan"][0]["status"] == "pending"
    assert should_after_verifier(result) == "replan"
