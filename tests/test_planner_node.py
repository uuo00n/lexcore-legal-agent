"""Planner 节点的结构化输出、边界与图路由测试。"""
from __future__ import annotations

import json

import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from agent.nodes.planner import PlannerOutput, TaskType, planner_node
from agent.nodes.routing import should_execute_next


class _FakeStructuredLLM:
    def __init__(self, output):
        self.output = output
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return self.output


class _FakePlannerLLM:
    def __init__(self, output):
        self.structured = _FakeStructuredLLM(output)
        self.schema = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self.structured

    def bind_tools(self, tools, **kwargs):
        raise AssertionError("Planner 不允许绑定工具")


async def test_planner_uses_structured_output_intent_and_writes_state(monkeypatch):
    fake_llm = _FakePlannerLLM({
        "steps": [
            {
                "step_id": "step_1",
                "task_type": "case_analysis",
                "description": "提取劳动关系、工作年限和解除事实",
                "assigned_agent": "case_analysis_agent",
                "status": "pending",
            },
            {
                "step_id": "step_2",
                "task_type": "statute_retrieval",
                "description": "检索违法解除和经济赔偿金的法律依据",
                "assigned_agent": "statute_retrieval_agent",
                "status": "pending",
            },
            {
                "step_id": "step_3",
                "task_type": "legal_consultation",
                "description": "综合事实和法条形成维权建议",
                "assigned_agent": "legal_consult_agent",
                "status": "pending",
            },
        ]
    })
    monkeypatch.setattr("agent.nodes.get_llm", lambda **kwargs: fake_llm)
    state = {
        "messages": [HumanMessage(content="公司违法解除劳动合同，我工作了五年，应如何维权？")],
        "intent": "labor",
        "intent_confidence": 0.9,
        "task_complexity": "medium",
        "supervisor_route": "case_analysis_agent",
    }

    result = await planner_node(state)

    assert fake_llm.schema is PlannerOutput
    assert result["plan"] == result["remaining_steps"]
    assert len(result["plan"]) == 3
    assert result["plan"][0]["task_type"] == TaskType.CASE_ANALYSIS
    assert should_execute_next(result) == "case_analysis_agent"
    payload = json.loads(fake_llm.structured.messages[-1].content)
    assert payload["intent"] == "labor"
    assert payload["complexity"] == "medium"


async def test_simple_statute_query_is_reduced_to_one_step(monkeypatch):
    fake_llm = _FakePlannerLLM({
        "steps": [
            {
                "step_id": "step_1",
                "task_type": "statute_retrieval",
                "description": "检索非法种植罂粟的处罚门槛",
                "assigned_agent": "statute_retrieval_agent",
                "status": "pending",
            },
            {
                "step_id": "step_2",
                "task_type": "legal_consultation",
                "description": "解释检索到的处罚门槛",
                "assigned_agent": "legal_consult_agent",
                "status": "pending",
            },
        ]
    })
    monkeypatch.setattr("agent.nodes.get_llm", lambda **kwargs: fake_llm)

    result = await planner_node({
        "messages": [HumanMessage(content="种植罂粟几株犯法？")],
        "intent": "statute_retrieval",
        "task_complexity": "low",
        "supervisor_route": "statute_retrieval_agent",
    })

    assert len(result["plan"]) == 1
    assert result["plan"][0]["task_type"] == TaskType.STATUTE_RETRIEVAL
    assert result["plan"][0]["assigned_agent"] == "statute_retrieval_agent"


async def test_non_legal_query_does_not_call_planner_model(monkeypatch):
    def fail_if_called(**kwargs):
        raise AssertionError("非法律问题不应调用 Planner 模型")

    monkeypatch.setattr("agent.nodes.get_llm", fail_if_called)

    result = await planner_node({
        "messages": [HumanMessage(content="今天天气怎么样？")],
        "intent": "non_legal",
        "supervisor_route": "end",
    })

    assert result == {"plan": [], "remaining_steps": []}


def test_planner_schema_rejects_more_than_six_steps():
    steps = [
        {
            "step_id": f"step_{index}",
            "task_type": "case_analysis",
            "description": f"分析第 {index} 组事实",
            "assigned_agent": "case_analysis_agent",
            "status": "pending",
        }
        for index in range(1, 8)
    ]

    with pytest.raises(ValidationError):
        PlannerOutput.model_validate({"steps": steps})


def test_planner_schema_rejects_invalid_enum_assignment_and_duplicate_step():
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate({
            "steps": [{
                "step_id": "step_1",
                "task_type": "statute_retrieval",
                "description": "检索法律依据",
                "assigned_agent": "case_analysis_agent",
                "status": "pending",
            }]
        })

    duplicate = {
        "task_type": "case_retrieval",
        "description": "检索相似案例",
        "assigned_agent": "case_analysis_agent",
        "status": "pending",
    }
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate({
            "steps": [
                {"step_id": "step_1", **duplicate},
                {"step_id": "step_2", **duplicate},
            ]
        })
