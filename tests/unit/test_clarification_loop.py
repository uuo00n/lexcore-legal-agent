"""澄清补问回归用例（§三十 用例 2、用例 3、§七、§八、§十五）。

覆盖五件事：
1. 简单劳动欠薪问题不追问，也不多付一次模型调用（用例 1 的事实闸门部分）；
2. 「公司辞退我，我能赔多少钱？」必须先补问，且答案里不得出现确定金额（用例 2）；
3. 同一 thread 内用户回复补问后走 Fact Merge → Fact Analysis → Complexity Router（用例 3）；
4. ``merge_confirmed_facts`` 不得让空值覆盖已确认事实；
5. 通用法律说明（General Advice）与个案法律结论（Individual Legal Conclusion）
   的阻断口径必须不同——前者先答再问，后者必须先问。
"""
from __future__ import annotations

import pytest

from agent.clarification import (
    MAX_CLARIFICATION_ROUNDS,
    decide_clarification,
    demands_individual_conclusion,
)
from agent.nodes.clarification import ORIGINAL_QUESTION_KEY, clarification_node, fact_merge_node
from agent.nodes.routing import should_after_fact_analysis
from agent.state import merge_confirmed_facts

SIMPLE_WAGE_QUESTION = "公司拖欠我三个月工资怎么办"
INDIVIDUAL_CONCLUSION_QUESTION = "公司辞退我，我能赔多少钱？"
GENERAL_RULE_QUESTION = "辞退员工需要支付多少赔偿金"


def _state(**overrides) -> dict:
    from langchain_core.messages import HumanMessage

    state = {
        "messages": [HumanMessage(content=INDIVIDUAL_CONCLUSION_QUESTION)],
        "thread_id": "thread-clarify",
        "trace_id": "",
    }
    state.update(overrides)
    return state


# ─── 确定性判定 ──────────────────────────────────────────────────────────


def test_simple_wage_arrears_question_does_not_trigger_clarification():
    decision = decide_clarification(SIMPLE_WAGE_QUESTION)

    assert decision.is_legal is True
    assert decision.facts_sufficient is True
    assert decision.needs_clarification is False
    assert decision.blocking is False
    assert decision.reason == "facts_sufficient"


def test_individual_conclusion_question_must_clarify_before_answering():
    decision = decide_clarification(INDIVIDUAL_CONCLUSION_QUESTION)

    assert decision.facts_sufficient is False
    assert decision.needs_clarification is True
    assert decision.blocking is True
    assert decision.must_ask_first is True
    assert decision.reason == "individual_conclusion"
    assert 1 <= len(decision.questions) <= 3


def test_general_rule_question_answers_first_without_blocking():
    decision = decide_clarification(GENERAL_RULE_QUESTION)

    assert decision.needs_clarification is False
    assert decision.blocking is False
    assert decision.reason == "legal_information_query"


def test_individual_conclusion_needs_both_own_case_and_conclusion_demand():
    assert demands_individual_conclusion(INDIVIDUAL_CONCLUSION_QUESTION) is True
    # 纯规则问题没有当事人，不该被追问打断。
    assert demands_individual_conclusion(GENERAL_RULE_QUESTION) is False
    # 有当事人但没有要确定结论，同样不阻断。
    assert demands_individual_conclusion("我想了解一下劳动仲裁的流程") is False


def test_confirmed_facts_make_a_terse_reply_sufficient():
    decision = decide_clarification(
        INDIVIDUAL_CONCLUSION_QUESTION,
        confirmed_facts={
            ORIGINAL_QUESTION_KEY: INDIVIDUAL_CONCLUSION_QUESTION,
            "用户补充0": "我干了3年，月薪8000元，有劳动合同和工资流水，上个月被辞退",
        },
    )

    assert decision.facts_sufficient is True
    assert decision.needs_clarification is False


def test_clarification_budget_stops_asking_but_keeps_missing_facts():
    decision = decide_clarification(
        INDIVIDUAL_CONCLUSION_QUESTION,
        round_count=MAX_CLARIFICATION_ROUNDS,
    )

    assert decision.reason == "clarification_budget_exhausted"
    assert decision.needs_clarification is False
    assert decision.blocking is False
    # 预算用尽不等于事实齐了：答案里仍要提示还缺什么。
    assert decision.facts_sufficient is False
    assert decision.missing_facts


def test_uploaded_document_is_read_before_asking_the_user():
    decision = decide_clarification(INDIVIDUAL_CONCLUSION_QUESTION, has_uploaded_doc=True)

    assert decision.reason == "uploaded_doc"
    assert decision.needs_clarification is False


# ─── State reducer ───────────────────────────────────────────────────────


def test_merge_confirmed_facts_never_lets_blank_overwrite_a_confirmed_fact():
    merged = merge_confirmed_facts(
        {"月工资": "8000 元", "工作年限": "3 年"},
        {"月工资": "", "工作年限": None, "证据": []},
    )

    assert merged == {"月工资": "8000 元", "工作年限": "3 年"}


def test_merge_confirmed_facts_accumulates_across_turns_and_supports_reset():
    merged = merge_confirmed_facts({"月工资": "8000 元"}, {"工作年限": "3 年"})
    assert merged == {"月工资": "8000 元", "工作年限": "3 年"}
    # 显式写入空字典是唯一的重置方式。
    assert merge_confirmed_facts(merged, {}) == {}


# ─── 路由与节点 ──────────────────────────────────────────────────────────


def test_blocking_clarification_routes_to_the_clarification_node():
    assert should_after_fact_analysis(
        _state(needs_clarification=True, clarification_blocking=True)
    ) == "clarification"


def test_non_blocking_clarification_continues_to_the_complexity_router():
    assert should_after_fact_analysis(
        _state(needs_clarification=True, clarification_blocking=False)
    ) == "complexity_router"


def test_finalized_turn_never_asks_the_user_again():
    assert should_after_fact_analysis(
        _state(needs_clarification=True, clarification_blocking=True, supervisor_finalized=True)
    ) == "complexity_router"


def test_clarification_node_asks_and_remembers_the_original_question():
    result = clarification_node(
        _state(
            rewritten_query=INDIVIDUAL_CONCLUSION_QUESTION,
            clarification_questions=["你在这家公司工作了多久？", "你的月工资是多少？"],
            missing_facts=["时间", "金额"],
        )
    )

    assert result["clarification_round"] == 1
    assert result["needs_clarification"] is True
    assert result["clarification_blocking"] is True
    assert result["supervisor_finalized"] is True
    assert result["confirmed_facts"] == {ORIGINAL_QUESTION_KEY: INDIVIDUAL_CONCLUSION_QUESTION}
    content = result["messages"][0].content
    assert "1. 你在这家公司工作了多久？" in content
    assert "2. 你的月工资是多少？" in content
    # 补问不得给出任何确定金额或结论（§三十 用例 2）。
    assert "赔偿金额为" not in content
    assert "元" not in content


def test_clarification_node_keeps_the_first_round_question_on_later_rounds():
    result = clarification_node(
        _state(
            clarification_round=1,
            confirmed_facts={ORIGINAL_QUESTION_KEY: INDIVIDUAL_CONCLUSION_QUESTION},
            clarification_questions=["你手上有哪些材料？"],
        )
    )

    assert result["clarification_round"] == 2
    # 第二轮的 latest message 是用户的补充，不能覆盖原始问题。
    assert "confirmed_facts" not in result


def test_fact_merge_is_a_no_op_on_an_ordinary_turn():
    assert fact_merge_node(_state()) == {"clarification_resumed": False}


def test_fact_merge_rebuilds_the_full_question_from_the_users_reply():
    from langchain_core.messages import AIMessage, HumanMessage

    state = _state(
        messages=[
            HumanMessage(content=INDIVIDUAL_CONCLUSION_QUESTION),
            AIMessage(content="为了给你准确的判断，我还需要确认几件事："),
            HumanMessage(content="3 年，月薪 8000"),
        ],
        needs_clarification=True,
        clarification_blocking=True,
        clarification_round=1,
        clarification_questions=["你在这家公司工作了多久？"],
        confirmed_facts={ORIGINAL_QUESTION_KEY: INDIVIDUAL_CONCLUSION_QUESTION},
    )

    result = fact_merge_node(state)

    assert result["clarification_resumed"] is True
    assert result["confirmed_facts"] == {"用户补充1": "3 年，月薪 8000"}
    assert result["rewritten_query"] == f"{INDIVIDUAL_CONCLUSION_QUESTION} 3 年，月薪 8000"
    # 上一轮的补问状态必须清掉，交给 Fact Analysis 重新判定，不能直接跳 Planner（§八）。
    assert result["needs_clarification"] is False
    assert result["clarification_blocking"] is False
    assert result["clarification_questions"] == []


@pytest.mark.parametrize("reply", ["", "   "])
def test_fact_merge_does_not_write_a_blank_supplement(reply):
    from langchain_core.messages import HumanMessage

    state = _state(
        messages=[HumanMessage(content=reply)],
        needs_clarification=True,
        clarification_blocking=True,
        clarification_round=1,
        confirmed_facts={ORIGINAL_QUESTION_KEY: INDIVIDUAL_CONCLUSION_QUESTION},
    )

    result = fact_merge_node(state)

    # reducer 会丢掉空值，已确认事实不会被清空（§八）。
    assert merge_confirmed_facts(
        {ORIGINAL_QUESTION_KEY: INDIVIDUAL_CONCLUSION_QUESTION},
        result["confirmed_facts"],
    ) == {ORIGINAL_QUESTION_KEY: INDIVIDUAL_CONCLUSION_QUESTION}
    assert result["rewritten_query"] == INDIVIDUAL_CONCLUSION_QUESTION
