"""Main LangGraph workflow integration tests with deterministic boundaries."""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.graph import build_graph


def _plan_for_route(route: str) -> list[dict]:
    if route == "case_analysis_agent":
        definitions = [
            ("case_analysis", "case_analysis_agent", "整理案件事实和证据"),
            ("statute_retrieval", "statute_retrieval_agent", "检索适用法规"),
            ("legal_consultation", "legal_consult_agent", "形成法律建议"),
        ]
    elif route == "statute_retrieval_agent":
        definitions = [("statute_retrieval", route, "检索指定法规")]
    else:
        definitions = [("legal_consultation", route, "回答法律咨询")]
    return [
        {
            "step_id": f"step_{index}",
            "task_type": task_type,
            "description": description,
            "assigned_agent": agent,
            "status": "pending",
            "required": True,
        }
        for index, (task_type, agent, description) in enumerate(definitions, start=1)
    ]


def _install_graph_fakes(monkeypatch, scenario: dict, law: dict, visited: list[str]):
    async def passthrough(_state):
        return {}

    async def router(_state):
        if scenario["route"] == "final":
            return {
                "intent_routed": True,
                "intent": "non_legal",
                "supervisor_route": "end",
                "supervisor_finalized": True,
                "messages": [AIMessage(content="该请求不是法律问题。")],
            }
        return {
            "intent_routed": True,
            "intent": "legal",
            "task_complexity": scenario["complexity"],
            "supervisor_route": scenario["route"],
            "supervisor_finalized": False,
        }

    async def planner(state):
        if state.get("intent") == "non_legal":
            return {"plan": [], "remaining_steps": []}
        plan = _plan_for_route(str(state["supervisor_route"]))
        return {"plan": plan, "remaining_steps": [dict(step) for step in plan]}

    def specialist(agent_name: str):
        async def run(state):
            visited.append(agent_name)
            step_id = str(state["current_step"])
            report = {
                "report_id": f"{step_id}:{agent_name}",
                "task_id": step_id,
                "agent_name": agent_name,
                "status": "completed",
                "summary": f"{agent_name} completed",
                "findings": {"analysis": "基于已有事实和法规形成结论"},
                "sources": [law] if agent_name != "case_analysis_agent" else [],
            }
            output = {"agent_reports": [report]}
            if agent_name == "statute_retrieval_agent":
                output["retrieved_laws"] = [law]
            return output

        return run

    async def verifier(_state):
        return {
            "verification_result": {
                "passed": True,
                "score": 1.0,
                "issues": [],
                "missing_sources": [],
                "invalid_citations": [],
                "needs_retry": False,
                "retry_reason": None,
            },
            "supervisor_route": "answer_generator",
        }

    async def answer(state):
        return {
            "messages": [AIMessage(content=f"已完成 {len(state.get('completed_steps', []))} 个步骤。")],
            "supervisor_finalized": True,
        }

    monkeypatch.setattr("agent.graph.context_compaction_node", passthrough)
    monkeypatch.setattr("agent.graph.memory_node", passthrough)
    monkeypatch.setattr("agent.graph.inject_doc_node", passthrough)
    monkeypatch.setattr("agent.graph.intent_router_node", router)
    monkeypatch.setattr("agent.graph.planner_node", planner)
    monkeypatch.setattr("agent.graph.case_analysis_agent_node", specialist("case_analysis_agent"))
    monkeypatch.setattr("agent.graph.statute_retrieval_agent_node", specialist("statute_retrieval_agent"))
    monkeypatch.setattr("agent.graph.legal_consult_agent_node", specialist("legal_consult_agent"))
    monkeypatch.setattr("agent.graph.result_verifier_node", verifier)
    monkeypatch.setattr("agent.graph.answer_generator_node", answer)


@pytest.mark.parametrize(
    ("scenario_name", "expected_agents"),
    [
        ("consultation", ["legal_consult_agent"]),
        ("statute_retrieval", ["statute_retrieval_agent"]),
        (
            "case_analysis",
            ["case_analysis_agent", "statute_retrieval_agent", "legal_consult_agent"],
        ),
    ],
)
async def test_main_graph_executes_legal_scenarios(
    monkeypatch,
    legal_scenarios,
    grounded_law,
    scenario_name,
    expected_agents,
):
    scenario = legal_scenarios[scenario_name]
    visited: list[str] = []
    _install_graph_fakes(monkeypatch, scenario, grounded_law, visited)

    result = await build_graph(checkpointer=None).ainvoke({
        "messages": [HumanMessage(content=scenario["query"])],
        "thread_id": f"graph-{scenario_name}",
    })

    assert visited == expected_agents
    assert all(step["status"] == "completed" for step in result["plan"])
    assert result["verification_result"]["passed"] is True
    assert result["messages"][-1].content == f"已完成 {len(expected_agents)} 个步骤。"


async def test_main_graph_ends_before_planning_specialists_for_non_legal_question(
    monkeypatch,
    legal_scenarios,
    grounded_law,
):
    scenario = legal_scenarios["non_legal"]
    visited: list[str] = []
    _install_graph_fakes(monkeypatch, scenario, grounded_law, visited)

    result = await build_graph(checkpointer=None).ainvoke({
        "messages": [HumanMessage(content=scenario["query"])],
        "thread_id": "graph-non-legal",
    })

    assert visited == []
    assert result["plan"] == []
    assert result["supervisor_finalized"] is True
    assert result["messages"][-1].content == "该请求不是法律问题。"
