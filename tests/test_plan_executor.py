"""Plan Executor state transitions and routing regressions."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from agent.graph import build_graph
from agent.nodes.routing import should_execute_next
from agent.nodes.supervisor import supervisor_agent_node
from agent.nodes.verifier import verify_plan_results
from services.supervisor import SupervisorDecision


def _plan() -> list[dict]:
    return [
        {
            "step_id": "step_1",
            "task_type": "case_analysis",
            "description": "整理案件事实",
            "assigned_agent": "case_analysis_agent",
            "status": "pending",
        },
        {
            "step_id": "step_2",
            "task_type": "statute_retrieval",
            "description": "检索法律依据",
            "assigned_agent": "statute_retrieval_agent",
            "status": "pending",
        },
    ]


async def test_supervisor_starts_first_pending_step_without_legal_analysis():
    result = await supervisor_agent_node({"plan": _plan()})

    assert result["supervisor_route"] == "case_analysis_agent"
    assert result["current_step"] == "step_1"
    assert result["plan"][0]["status"] == "running"
    assert result["plan"][1]["status"] == "pending"
    assert should_execute_next(result) == "case_analysis_agent"


async def test_supervisor_completes_running_step_and_starts_next_step():
    plan = _plan()
    plan[0]["status"] = "running"
    report = {
        "report_id": "step_1:case_analysis_agent",
        "task_id": "step_1",
        "agent_name": "case_analysis_agent",
        "summary": "事实已整理",
    }

    result = await supervisor_agent_node({
        "plan": plan,
        "current_step": "step_1",
        "agent_reports": [report],
    })

    assert result["plan"][0]["status"] == "completed"
    assert result["plan"][0]["result"] == report
    assert result["completed_steps"][0]["step_id"] == "step_1"
    assert result["remaining_steps"] == [result["plan"][1]]
    assert result["plan"][1]["status"] == "running"
    assert result["current_step"] == "step_2"
    assert result["supervisor_route"] == "statute_retrieval_agent"


async def test_supervisor_routes_to_verify_after_every_step_completes():
    plan = _plan()[:1]
    plan[0]["status"] = "running"
    report = {
        "report_id": "step_1:case_analysis_agent",
        "task_id": "step_1",
        "agent_name": "case_analysis_agent",
        "summary": "事实已整理",
    }

    result = await supervisor_agent_node({
        "plan": plan,
        "current_step": "step_1",
        "agent_reports": [report],
    })

    assert result["plan"][0]["status"] == "completed"
    assert result["remaining_steps"] == []
    assert result["supervisor_route"] == "verify"
    assert should_execute_next(result) == "verify"
    verification = verify_plan_results({
        "plan": result["plan"],
        "agent_reports": [report],
    })
    assert verification["passed"] is True


async def test_supervisor_retries_running_step_without_a_report():
    plan = _plan()[:1]
    plan[0]["status"] = "running"

    result = await supervisor_agent_node({
        "plan": plan,
        "current_step": "step_1",
        "agent_reports": [],
        "retry_count": 0,
        "tool_call_count": 4,
    })

    assert result["plan"][0]["status"] == "running"
    assert result["retry_count"] == 1
    assert result["tool_call_count"] == 0
    assert result["supervisor_route"] == "case_analysis_agent"


def test_should_execute_next_has_only_executor_destinations():
    assert should_execute_next({"supervisor_route": "verify"}) == "verify"
    assert should_execute_next({"supervisor_route": "end"}) == "end"
    assert should_execute_next({
        "plan": [{
            "step_id": "step_1",
            "assigned_agent": "legal_consult_agent",
            "status": "running",
        }],
    }) == "legal_consult_agent"


async def test_graph_executes_plan_sequentially_before_verifier(monkeypatch):
    execution_order: list[str] = []

    class _StructuredPlanner:
        async def ainvoke(self, _messages):
            return {
                "steps": [
                    {
                        "step_id": "step_1",
                        "task_type": "case_analysis",
                        "description": "整理案件事实",
                        "assigned_agent": "case_analysis_agent",
                        "status": "pending",
                    },
                    {
                        "step_id": "step_2",
                        "task_type": "statute_retrieval",
                        "description": "检索法律依据",
                        "assigned_agent": "statute_retrieval_agent",
                        "status": "pending",
                    },
                ]
            }

    class _PlannerAndVerifierLLM:
        def with_structured_output(self, _schema):
            return _StructuredPlanner()

        async def ainvoke(self, _messages):
            execution_order.append("verifier")
            return AIMessage(content="核验后的最终答复")

    async def fake_route(**_kwargs):
        return SupervisorDecision(
            route="case_analysis_agent",
            reason="需要执行计划",
            complexity="medium",
            need_tools=True,
        )

    async def fake_case(state):
        execution_order.append("case_analysis_agent")
        step_id = state["current_step"]
        return {"agent_reports": [{
            "report_id": f"{step_id}:case_analysis_agent",
            "task_id": step_id,
            "agent_name": "case_analysis_agent",
            "summary": "事实已整理",
        }]}

    async def fake_statute(state):
        execution_order.append("statute_retrieval_agent")
        step_id = state["current_step"]
        return {"agent_reports": [{
            "report_id": f"{step_id}:statute_retrieval_agent",
            "task_id": step_id,
            "agent_name": "statute_retrieval_agent",
            "summary": "法条已检索",
        }]}

    async def passthrough(_state):
        return {}

    monkeypatch.setattr("agent.nodes.route_user_request_with_llm", fake_route)
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: _PlannerAndVerifierLLM())
    monkeypatch.setattr("agent.graph.context_compaction_node", passthrough)
    monkeypatch.setattr("agent.graph.memory_node", passthrough)
    monkeypatch.setattr("agent.graph.inject_doc_node", passthrough)
    monkeypatch.setattr("agent.graph.case_analysis_agent_node", fake_case)
    monkeypatch.setattr("agent.graph.statute_retrieval_agent_node", fake_statute)

    result = await build_graph().ainvoke({
        "messages": [HumanMessage(content="公司解除劳动合同后如何维权？")],
    })

    assert execution_order == [
        "case_analysis_agent",
        "statute_retrieval_agent",
        "verifier",
    ]
    assert [step["status"] for step in result["plan"]] == ["completed", "completed"]
    assert [step["step_id"] for step in result["completed_steps"]] == ["step_1", "step_2"]
    assert result["remaining_steps"] == []
    assert result["verification_result"]["passed"] is True
    assert result["messages"][-1].content == "核验后的最终答复"
