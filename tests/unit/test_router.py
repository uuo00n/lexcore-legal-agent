"""Intent Router tests for the primary request classes."""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.nodes.supervisor import intent_router_node
from services.supervisor import SupervisorDecision


@pytest.mark.parametrize(
    "scenario_name",
    ["consultation", "statute_retrieval", "case_analysis"],
)
async def test_router_dispatches_legal_requests(monkeypatch, legal_scenarios, scenario_name):
    scenario = legal_scenarios[scenario_name]

    async def fake_route(**kwargs):
        assert kwargs["message"] == scenario["query"]
        return SupervisorDecision(
            route=scenario["route"],
            reason=f"route {scenario_name}",
            complexity=scenario["complexity"],
            need_tools=True,
        )

    monkeypatch.setattr("agent.nodes.route_user_request_with_llm", fake_route)

    result = await intent_router_node({
        "messages": [HumanMessage(content=scenario["query"])],
        "thread_id": "router-test",
    })

    assert result["intent_routed"] is True
    assert result["supervisor_route"] == scenario["route"]
    assert result["task_complexity"] == scenario["complexity"]
    assert result["supervisor_finalized"] is False


async def test_router_short_circuits_non_legal_question(monkeypatch, legal_scenarios):
    scenario = legal_scenarios["non_legal"]

    async def fake_route(**_kwargs):
        return SupervisorDecision(
            route="final",
            reason="非法律创作请求",
            complexity="low",
            need_tools=False,
        )

    class FakeLLM:
        async def ainvoke(self, _messages):
            return AIMessage(content="我主要处理法律问题。")

    monkeypatch.setattr("agent.nodes.route_user_request_with_llm", fake_route)
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: FakeLLM())

    result = await intent_router_node({
        "messages": [HumanMessage(content=scenario["query"])],
    })

    assert result["intent"] == "non_legal"
    assert result["supervisor_route"] == "end"
    assert result["supervisor_finalized"] is True
    assert result["messages"][0].content == "我主要处理法律问题。"
