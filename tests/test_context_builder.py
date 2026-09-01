"""Context Engineering budget, Top-N evidence, and tool-summary regressions."""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from services.context_builder import ContextBudget, build_model_context


def _budget(**overrides) -> ContextBudget:
    values = {
        "input_tokens": 1800,
        "output_reserve": 300,
        "system": 300,
        "relevant_memory": 220,
        "summary": 180,
        "recent_messages": 420,
        "current_plan": 180,
        "evidence": 260,
        "current_task": 160,
        "tool_result": 120,
        "max_recent_messages": 6,
        "law_top_n": 2,
        "case_top_n": 1,
    }
    values.update(overrides)
    return ContextBudget(**values)


def test_builder_layers_context_and_never_injects_unbounded_history():
    messages = []
    for index in range(20):
        messages.extend([
            HumanMessage(content=f"旧问题 {index} " + "甲" * 80),
            AIMessage(content=f"旧回答 {index} " + "乙" * 80),
        ])
    state = {
        "messages": messages,
        "memory_profile": "身份：员工",
        "memory_longterm": "用户偏好先看风险",
        "memory_summary": "此前持续讨论劳动合同解除。",
        "plan": [{"step_id": "step_1", "description": "检索法规"}],
    }

    built = build_model_context(state, "基础系统提示", task_context={"query": "怎么办"}, budget=_budget())

    assert len(built.messages) <= 7  # system + bounded recent suffix
    assert "Relevant Memory" in built.system_prompt
    assert "Conversation Summary" in built.system_prompt
    assert "Current Plan" in built.system_prompt
    assert built.status["source_message_count"] == 40
    assert built.status["estimated_prompt_tokens"] <= built.status["prompt_token_budget"]


def test_large_tool_result_is_summarized_before_model_injection():
    raw = json.dumps({
        "status": "found",
        "source_type": "local_rag",
        "results": [
            {"law_name": f"法律{i}", "article_no": f"第{i}条", "content": "正文" * 300}
            for i in range(12)
        ],
    }, ensure_ascii=False)
    state = {
        "messages": [
            HumanMessage(content="请查相关法条"),
            AIMessage(content="", tool_calls=[{
                "name": "retrieve_local_law_tool",
                "args": {"query": "劳动合同解除"},
                "id": "call-1",
            }]),
            ToolMessage(content=raw, tool_call_id="call-1", name="retrieve_local_law_tool"),
        ]
    }

    built = build_model_context(state, "系统", budget=_budget(recent_messages=900))
    tool_message = next(item for item in built.messages if isinstance(item, ToolMessage))

    assert len(tool_message.content) < len(raw)
    assert '"_context_summary":true' in tool_message.content
    assert built.status["tool_results_summarized"] == 1


def test_retrieved_evidence_is_top_n_and_score_ordered():
    state = {
        "messages": [HumanMessage(content="问题")],
        "retrieved_laws": [
            {"law_name": "低", "score": 0.1},
            {"law_name": "高", "score": 0.9},
            {"law_name": "中", "score": 0.5},
        ],
        "retrieved_cases": [
            {"case_name": "案例低", "score": 0.2},
            {"case_name": "案例高", "score": 0.8},
        ],
    }

    built = build_model_context(state, "系统", budget=_budget())

    assert [item["law_name"] for item in built.selected_laws] == ["高", "中"]
    assert [item["case_name"] for item in built.selected_cases] == ["案例高"]
    assert "案例低" not in built.system_prompt


def test_working_state_reducer_keeps_only_configured_top_n(monkeypatch):
    from agent.state import merge_retrieved_laws

    monkeypatch.setenv("CONTEXT_RETRIEVED_LAW_TOP_N", "2")
    merged = merge_retrieved_laws(
        [{"law_name": "旧低分", "score": 0.1}],
        [
            {"law_name": "新高分", "score": 0.9},
            {"law_name": "新中分", "score": 0.5},
        ],
    )

    assert [item["law_name"] for item in merged] == ["新高分", "新中分"]


def test_hard_budget_survives_overallocated_operator_configuration():
    budget = _budget(
        input_tokens=300,
        output_reserve=100,
        system=500,
        relevant_memory=500,
        recent_messages=500,
        evidence=500,
    )
    built = build_model_context(
        {
            "messages": [HumanMessage(content="最新问题" + "甲" * 600)],
            "memory_longterm": "长期记忆" * 300,
            "retrieved_laws": [{"law_name": "民法典", "content": "正文" * 500}],
        },
        "系统规则" * 300,
        budget=budget,
    )

    assert built.status["estimated_prompt_tokens"] <= 200
