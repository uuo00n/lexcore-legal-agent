"""Context Engineering budget, Top-N evidence, and tool-summary regressions."""
from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from services.context_builder import (
    DEFAULT_TIER,
    TIERS,
    ContextBudget,
    budget_for_tier,
    build_model_context,
    max_tier_recent_messages,
    resolve_context_tier,
    retained_law_top_n,
)
from services.context_builder import _LAYER_ENV

_TIER_SUFFIXES = ("INPUT_TOKENS", "OUTPUT_RESERVE", "RECENT_MESSAGE_COUNT", "LAW_TOP_N", "CASE_TOP_N")


@pytest.fixture
def tier_env(monkeypatch):
    """把档位口径固定在文档承诺的基准上，避免开发机 .env 的覆盖值让断言漂移。"""
    monkeypatch.setenv("CONTEXT_MODEL_MAX_TOKENS", "128000")
    monkeypatch.setenv("CONTEXT_INPUT_TOKEN_BUDGET", "64000")
    monkeypatch.setenv("CONTEXT_OUTPUT_TOKEN_RESERVE", "8000")
    monkeypatch.setenv("CONTEXT_RECENT_MESSAGE_COUNT", "12")
    monkeypatch.setenv("CONTEXT_RETRIEVED_LAW_TOP_N", "6")
    monkeypatch.setenv("CONTEXT_RETRIEVED_CASE_TOP_N", "4")
    monkeypatch.setenv("CONTEXT_LONG_MATERIAL_TOKENS", "4000")
    monkeypatch.setenv("CONTEXT_LONG_CASE_COUNT", "8")
    for name in _LAYER_ENV.values():
        monkeypatch.delenv(name, raising=False)
    for tier in TIERS:
        for suffix in _TIER_SUFFIXES:
            monkeypatch.delenv(f"CONTEXT_TIER_{tier.upper()}_{suffix}", raising=False)


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


def test_working_state_reducer_retains_up_to_the_largest_tier_top_n(tier_env, monkeypatch):
    from agent.state import merge_retrieved_laws

    # 标准档只喂 2 条，但工作态要为长上下文档留够（2 × 2.5 = 5 条）。
    monkeypatch.setenv("CONTEXT_RETRIEVED_LAW_TOP_N", "2")
    merged = merge_retrieved_laws(
        [{"law_name": "旧低分", "score": 0.0}],
        [{"law_name": f"法条{index}", "score": index / 10} for index in range(7)],
    )

    assert retained_law_top_n() == 5
    assert [item["law_name"] for item in merged] == ["法条6", "法条5", "法条4", "法条3", "法条2"]


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


def test_tier_budgets_match_documented_context_window(tier_env):
    expected = {
        # 档位: (输入预算, 输出预留, 目标使用量区间)
        "standard": (32_000, 8_000, (16_000, 32_000)),
        "complex": (64_000, 12_000, (32_000, 64_000)),
        "long": (128_000, 16_000, (64_000, 128_000)),
    }

    for tier, (input_tokens, reserve, band) in expected.items():
        budget = budget_for_tier(tier)
        assert (budget.input_tokens, budget.output_reserve) == (input_tokens, reserve)
        assert budget.target_input_tokens == band
        assert budget.prompt_tokens == input_tokens - reserve

    # 单次任务默认预算 64K：复杂度路由还没结论时用它。
    default_budget = ContextBudget()
    assert (default_budget.tier, default_budget.input_tokens) == (DEFAULT_TIER, 64_000)

    # 窗口放大必须同时放大条数，否则大预算只是空转。
    assert budget_for_tier("standard").law_top_n < budget_for_tier("long").law_top_n
    assert budget_for_tier("standard").case_top_n < budget_for_tier("long").case_top_n
    assert budget_for_tier("long").max_recent_messages == max_tier_recent_messages() == 30


def test_layer_budgets_scale_with_tier_and_stay_inside_prompt_budget(tier_env):
    for tier in TIERS:
        budget = budget_for_tier(tier)
        layers = (
            budget.system, budget.relevant_memory, budget.summary, budget.recent_messages,
            budget.current_plan, budget.evidence, budget.current_task,
        )
        assert sum(layers) <= budget.prompt_tokens

    assert budget_for_tier("long").evidence > budget_for_tier("standard").evidence * 3


def test_model_max_tokens_clamps_every_tier(tier_env, monkeypatch):
    monkeypatch.setenv("CONTEXT_MODEL_MAX_TOKENS", "32000")

    for tier in TIERS:
        budget = budget_for_tier(tier)
        assert budget.input_tokens <= 32_000
        assert budget.prompt_tokens > 0
        assert budget.output_reserve < budget.input_tokens


def test_tier_resolution_follows_material_size_and_complexity(tier_env):
    assert resolve_context_tier({"complexity_level": "simple"}).tier == "standard"
    assert resolve_context_tier({"complexity_level": "medium"}).tier == "standard"
    assert resolve_context_tier({"complexity_level": "complex"}).tier == "complex"
    assert resolve_context_tier({"task_complexity": "high"}).tier == "complex"
    # 复杂度路由还没跑（例如 Fact Analysis 之前）时退回单次任务默认预算。
    assert resolve_context_tier({}).tier == DEFAULT_TIER

    long_contract = resolve_context_tier({
        "complexity_level": "simple",
        "uploaded_doc_text": "合同条款" * 3000,
    })
    assert long_contract.tier == "long"
    assert "long_material" in long_contract.signals

    many_cases = resolve_context_tier({
        "complexity_level": "simple",
        "retrieved_cases": [{"case_name": f"案例{index}"} for index in range(8)],
    })
    assert many_cases.tier == "long"
    assert "many_cases" in many_cases.signals


def test_build_model_context_applies_resolved_tier_and_reports_it(tier_env):
    laws = [{"law_name": f"法律{index}", "content": "正文", "score": index / 10} for index in range(8)]
    state = {
        "messages": [HumanMessage(content="加班费怎么算")],
        "complexity_level": "simple",
        "retrieved_laws": laws,
    }

    standard = build_model_context(state, "系统", task_context={"query": "加班费"})

    assert standard.status["context_tier"] == "standard"
    assert standard.status["input_token_budget"] == 32_000
    assert standard.status["output_token_reserve"] == 8_000
    assert standard.status["target_input_tokens"] == [16_000, 32_000]
    assert standard.status["model_max_tokens"] == 128_000
    assert standard.status["selected_law_count"] == 6
    assert standard.status["estimated_input_tokens"] == (
        standard.status["estimated_prompt_tokens"] + 8_000
    )

    # 同一个问题挂上长合同后升档，证据条数与预算一起放开。
    escalated = build_model_context(
        {**state, "uploaded_doc_text": "合同条款" * 3000},
        "系统",
        task_context={"query": "加班费"},
    )

    assert escalated.status["context_tier"] == "long"
    assert escalated.status["input_token_budget"] == 128_000
    assert escalated.status["selected_law_count"] == 8
    assert escalated.status["estimated_prompt_tokens"] > standard.status["estimated_prompt_tokens"]


def test_explicit_budget_still_wins_over_tier_resolution(tier_env):
    built = build_model_context(
        {"messages": [HumanMessage(content="问题")], "uploaded_doc_text": "合同条款" * 3000},
        "系统",
        budget=_budget(),
    )

    assert built.status["tier_reason"] == "explicit_budget"
    assert built.status["input_token_budget"] == 1800
    assert built.status["estimated_prompt_tokens"] <= built.status["prompt_token_budget"]
