"""Planner tests for one-step and multi-step execution plans."""
from __future__ import annotations

from langchain_core.messages import HumanMessage

from agent.nodes.planner import PlannerOutput, planner_node
from agent.state import TaskType


class FakeStructuredPlanner:
    def __init__(self, output):
        self.output = output

    async def ainvoke(self, _messages):
        return self.output


class FakePlannerLLM:
    def __init__(self, output):
        self.output = output
        self.schema = None

    def with_structured_output(self, schema):
        self.schema = schema
        return FakeStructuredPlanner(self.output)


async def test_planner_builds_case_analysis_multi_step_plan(monkeypatch, legal_scenarios):
    scenario = legal_scenarios["case_analysis"]
    fake_llm = FakePlannerLLM({
        "steps": [
            {
                "step_id": "step_1",
                "task_type": "case_analysis",
                "description": "整理劳动关系、解除行为和已有证据",
                "assigned_agent": "case_analysis_agent",
            },
            {
                "step_id": "step_2",
                "task_type": "statute_retrieval",
                "description": "检索违法解除和赔偿金的现行规定",
                "assigned_agent": "statute_retrieval_agent",
            },
            {
                "step_id": "step_3",
                "task_type": "case_retrieval",
                "description": "检索事实结构相近的劳动争议案例",
                "assigned_agent": "case_analysis_agent",
            },
            {
                "step_id": "step_4",
                "task_type": "legal_consultation",
                "description": "综合证据和法律依据给出行动建议",
                "assigned_agent": "legal_consult_agent",
            },
        ]
    })
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: fake_llm)

    result = await planner_node({
        "messages": [HumanMessage(content=scenario["query"])],
        "intent": "labor_dispute",
        "task_complexity": "high",
        "supervisor_route": "case_analysis_agent",
    })

    assert fake_llm.schema is PlannerOutput
    assert [step["task_type"] for step in result["plan"]] == [
        TaskType.CASE_ANALYSIS,
        TaskType.STATUTE_RETRIEVAL,
        TaskType.CASE_RETRIEVAL,
        TaskType.LEGAL_CONSULTATION,
    ]
    assert all(step["status"] == "pending" for step in result["plan"])
    assert result["remaining_steps"] == result["plan"]


async def test_planner_keeps_plain_statute_lookup_to_one_step(monkeypatch, legal_scenarios):
    scenario = legal_scenarios["statute_retrieval"]
    fake_llm = FakePlannerLLM({
        "steps": [{
            "step_id": "step_1",
            "task_type": "statute_retrieval",
            "description": "检索指定法条",
            "assigned_agent": "statute_retrieval_agent",
        }]
    })
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: fake_llm)

    result = await planner_node({
        "messages": [HumanMessage(content=scenario["query"])],
        "intent": "statute_retrieval",
        "task_complexity": "low",
        "supervisor_route": "statute_retrieval_agent",
    })

    assert len(result["plan"]) == 1
    assert result["plan"][0]["assigned_agent"] == "statute_retrieval_agent"


async def test_planner_skips_non_legal_question(monkeypatch, legal_scenarios):
    monkeypatch.setattr(
        "agent.nodes.get_llm",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("model must not run")),
    )
    result = await planner_node({
        "messages": [HumanMessage(content=legal_scenarios["non_legal"]["query"])],
        "intent": "non_legal",
        "supervisor_route": "end",
    })

    assert result == {"plan": [], "remaining_steps": []}
