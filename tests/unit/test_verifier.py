"""Result Verifier failure and bounded replan tests."""
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


async def test_first_verifier_failure_resets_plan_for_one_replan(monkeypatch, grounded_law):
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: EmptyVerifierLLM())

    result = await result_verifier_node(_failed_state(grounded_law))

    assert result["verification_result"]["passed"] is False
    assert result["replan_retry_count"] == 1
    assert result["supervisor_route"] == "replan"
    assert result["plan"][0]["status"] == "pending"
    assert result["agent_reports"] == []
    assert should_after_verifier(result) == "replan"


async def test_second_verifier_failure_stops_retry_loop(monkeypatch, grounded_law):
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: EmptyVerifierLLM())
    state = _failed_state(grounded_law)
    state["replan_retry_count"] = 1

    result = await result_verifier_node(state)

    assert result["verification_result"]["passed"] is False
    assert result["verification_result"]["needs_retry"] is False
    assert result["supervisor_route"] == "answer_generator"
    assert should_after_verifier(result) == "answer_generator"
