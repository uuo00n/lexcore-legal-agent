"""Planner tests for one-step and multi-step execution plans."""
from __future__ import annotations

from langchain_core.messages import HumanMessage

from agent.nodes.planner import PlannerOutput, planner_node
from agent.state import TaskType
from infrastructure.operational_store import InMemoryOperationalStore, init_operational_store
from services.checkpoint import reset_for_tests
from services.observability import create_trace, get_trace


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
                "assigned_agent": "case_retrieval_agent",
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
        # §五：类案检索由 Complexity Router 决定；只有它点头才允许保留案例检索步骤。
        "needs_case_retrieval": True,
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


async def test_planner_drops_case_retrieval_when_the_router_says_it_is_not_needed(
    monkeypatch, legal_scenarios
):
    """§五：needs_case_retrieval 为假时，模型规划的案例检索步骤必须被代码删掉。

    这条约束不能只写在 Prompt 里（§三十一「不要把代码该做的事推给 Prompt」）：
    普通法条咨询多查一轮案例，既拖慢简单问题，也会引入无关证据。
    """
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
                "task_type": "case_retrieval",
                "description": "检索事实结构相近的劳动争议案例",
                "assigned_agent": "case_retrieval_agent",
            },
            {
                "step_id": "step_3",
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
        "needs_case_retrieval": False,
    })

    assert [step["task_type"] for step in result["plan"]] == [
        TaskType.CASE_ANALYSIS,
        TaskType.LEGAL_CONSULTATION,
    ]
    # 删掉步骤之后编号必须重新连续，否则 Supervisor 会按空洞的 step_id 调度。
    assert [step["step_id"] for step in result["plan"]] == ["step_1", "step_2"]


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
    # §P1-5：模型正常返回时不得挂着降级标记，否则一次抖动会污染后续所有轮次。
    assert result["planner_degraded"] is False


class RaisingPlannerLLM:
    """结构化输出阶段抛错的 Provider 替身（§三十 用例 9）。"""

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        raise RuntimeError("planner provider 503")


async def test_planner_provider_error_falls_back_and_marks_degraded(monkeypatch, legal_scenarios):
    """§三十 用例 9、§P1-5：Planner Provider 报错 → 兜底计划 + 显式降级标记，不抛异常。

    兜底本身是既有能力，这里锁的是「降级必须看得见」：State 上有 ``planner_degraded``，
    Trace 上有 ``planner_degraded`` 事件，否则一条模型不可用产出的计划和正常规划无从区分。
    """
    reset_for_tests()
    init_operational_store(InMemoryOperationalStore())
    try:
        create_trace("trace-planner", "thread-planner", "公司违法解除劳动合同怎么赔")
        monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: RaisingPlannerLLM())

        result = await planner_node({
            "messages": [HumanMessage(content=legal_scenarios["case_analysis"]["query"])],
            "intent": "labor_dispute",
            "task_complexity": "high",
            "supervisor_route": "case_analysis_agent",
            "needs_case_retrieval": False,
            "trace_id": "trace-planner",
            "thread_id": "thread-planner",
        })

        # 计划可执行：Supervisor 拿到的仍是编号连续、可分派的步骤，链路不中断。
        assert result["planner_degraded"] is True
        assert [step["step_id"] for step in result["plan"]] == ["step_1", "step_2", "step_3"]
        assert [step["task_type"] for step in result["plan"]] == [
            TaskType.CASE_ANALYSIS,
            TaskType.STATUTE_RETRIEVAL,
            TaskType.LEGAL_CONSULTATION,
        ]
        assert all(step["status"] == "pending" for step in result["plan"])
        assert result["remaining_steps"] == result["plan"]

        events = {event["event_type"]: event for event in get_trace("trace-planner")["events"]}
        assert events["planner_degraded"]["payload"]["reason"] == "planner_llm_unavailable"
        assert "planner provider 503" in events["agent_fallback"]["payload"]["error"]
        assert events["plan_created"]["payload"]["planner_degraded"] is True
    finally:
        reset_for_tests()


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
