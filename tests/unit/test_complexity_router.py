"""Complexity Router 的定档与简单路径（§九、§P1-1、§五、§三十 用例 1）。

覆盖四件事：
1. 单一争议焦点的问题走简单路径：固定最小计划、不进 Planner、不查类案；
2. 需要拆解的问题（上传文书、明确要判例、多争议焦点、跨领域、长案情）升档走
   Plan-and-Execute，规划仍然交给 Planner；
3. 类案检索只有在用户明确要求时才打开（§五），简单法条咨询一律不查；
4. 复杂度只有一个真相源：既有的 ``task_complexity`` 由本节点覆写（§二 问题 12）。
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage

from agent.complexity import decide_complexity, demands_case_retrieval
from agent.nodes.complexity import complexity_router_node
from agent.nodes.routing import should_after_complexity
from agent.state import TaskType

SIMPLE_WAGE_QUESTION = "公司拖欠我三个月工资怎么办"
CASE_LAW_QUESTION = "公司拖欠工资，法院一般怎么判，有没有类似案例"
CROSS_DOMAIN_QUESTION = "老板欠款一直不还，还拖欠我的工资"
LONG_NARRATIVE = "我在这家公司工作了三年，" + "去年开始公司经常安排我加班但从来不给加班费，" * 8


def _state(question: str = SIMPLE_WAGE_QUESTION, **overrides) -> dict:
    state = {
        "messages": [HumanMessage(content=question)],
        "rewritten_query": question,
        "intent": "labor",
        "thread_id": "thread-complexity",
        "trace_id": "",
    }
    state.update(overrides)
    return state


# ─── 确定性判定 ──────────────────────────────────────────────────────────


def test_single_issue_question_takes_the_simple_route():
    decision = decide_complexity(SIMPLE_WAGE_QUESTION)

    assert decision.level == "simple"
    assert decision.execution_mode == "simple"
    assert decision.reason == "single_issue"
    # §三十 用例 1：普通欠薪问题默认不查类案。
    assert decision.needs_case_retrieval is False
    assert decision.legacy_task_complexity == "low"


def test_case_law_demand_escalates_and_turns_on_case_retrieval():
    decision = decide_complexity(CASE_LAW_QUESTION)

    assert decision.level == "complex"
    assert decision.execution_mode == "plan"
    assert decision.needs_case_retrieval is True
    assert "case_demand" in decision.signals
    assert demands_case_retrieval(CASE_LAW_QUESTION) is True
    assert demands_case_retrieval(SIMPLE_WAGE_QUESTION) is False


def test_uploaded_document_escalates_without_asking_for_cases():
    decision = decide_complexity(SIMPLE_WAGE_QUESTION, has_uploaded_doc=True)

    assert decision.level == "complex"
    assert decision.reason == "uploaded_doc"
    # 升档只说明需要拆解，不等于需要类案（§五）。
    assert decision.needs_case_retrieval is False


def test_three_disputed_issues_are_complex_and_two_are_medium():
    complex_decision = decide_complexity(
        SIMPLE_WAGE_QUESTION,
        case_facts={"legal_issues": ["欠薪是否成立", "能否解除劳动合同", "加班费如何计算"]},
    )
    medium_decision = decide_complexity(
        SIMPLE_WAGE_QUESTION,
        case_facts={"legal_issues": ["欠薪是否成立", "能否解除劳动合同"]},
    )

    assert complex_decision.level == "complex"
    assert "many_legal_issues" in complex_decision.signals
    assert medium_decision.level == "medium"
    assert medium_decision.execution_mode == "plan"
    assert "multiple_legal_issues" in medium_decision.signals


def test_repeated_issue_text_does_not_inflate_the_complexity():
    decision = decide_complexity(
        SIMPLE_WAGE_QUESTION,
        case_facts={"legal_issues": ["欠薪是否成立", "欠薪是否成立", " ", ""]},
    )

    assert decision.level == "simple"


def test_router_high_complexity_is_respected():
    assert decide_complexity(SIMPLE_WAGE_QUESTION, router_complexity="high").level == "complex"
    # 只有 high 才升档；medium 仍然由本模块自己判定，避免两套口径并存。
    assert decide_complexity(SIMPLE_WAGE_QUESTION, router_complexity="medium").level == "simple"


def test_cross_domain_and_long_narrative_need_the_planner():
    cross_domain = decide_complexity(CROSS_DOMAIN_QUESTION)
    narrative = decide_complexity(LONG_NARRATIVE)

    assert cross_domain.level == "medium"
    assert "cross_domain" in cross_domain.signals
    assert narrative.level == "medium"
    assert "long_narrative" in narrative.signals


def test_multiple_demands_and_parties_need_the_planner():
    assert decide_complexity("公司拖欠工资怎么办，另外能不能要求加班费").level == "medium"
    assert decide_complexity("工程转包之后拖欠我工资该找谁").level == "medium"


def test_exhausted_clarification_budget_still_goes_through_the_planner():
    decision = decide_complexity(SIMPLE_WAGE_QUESTION, clarification_exhausted=True)

    assert decision.level == "medium"
    assert "clarification_exhausted" in decision.signals


def test_non_legal_request_never_takes_the_simple_route():
    decision = decide_complexity("今天天气怎么样", is_legal=False)

    assert decision.level == "medium"
    assert decision.execution_mode == "plan"
    assert decision.reason == "not_legal"
    # 空问题同样不能落进固定最小计划。
    assert decide_complexity("   ").execution_mode == "plan"


# ─── 节点行为 ────────────────────────────────────────────────────────────


def test_simple_route_writes_a_fixed_two_step_plan():
    result = complexity_router_node(_state())

    assert result["complexity_level"] == "simple"
    assert result["execution_mode"] == "simple"
    assert result["needs_case_retrieval"] is False
    assert [step["task_type"] for step in result["plan"]] == [
        TaskType.STATUTE_RETRIEVAL,
        TaskType.LEGAL_CONSULTATION,
    ]
    assert [step["step_id"] for step in result["plan"]] == ["step_1", "step_2"]
    assert [step["assigned_agent"] for step in result["plan"]] == [
        "statute_retrieval_agent",
        "legal_consult_agent",
    ]
    assert all(step["status"] == "pending" for step in result["plan"])
    # remaining_steps 必须是副本，Supervisor 消费它时不能改动 plan。
    assert result["remaining_steps"] == result["plan"]
    assert result["remaining_steps"][0] is not result["plan"][0]


def test_plan_route_leaves_the_planning_to_the_planner():
    result = complexity_router_node(_state(CASE_LAW_QUESTION))

    assert result["execution_mode"] == "plan"
    assert result["needs_case_retrieval"] is True
    assert result["task_complexity"] == "high"
    # 复杂路径不预置计划，否则会和 Planner 的结果打架。
    assert "plan" not in result
    assert "remaining_steps" not in result


def test_complexity_router_overwrites_a_stale_router_verdict():
    """§二 问题 12：复杂度只有一个真相源，既有字段跟着路由结论走。"""
    result = complexity_router_node(_state(task_complexity="medium"))

    assert result["complexity_level"] == "simple"
    assert result["task_complexity"] == "low"


def test_chitchat_intent_does_not_get_a_statute_retrieval_plan():
    result = complexity_router_node(_state("你好呀", intent="non_legal"))

    assert result["execution_mode"] == "plan"
    assert "plan" not in result


def test_finalized_turn_is_a_no_op():
    assert complexity_router_node(_state(supervisor_finalized=True)) == {}


def test_uploaded_document_state_escalates_the_node_too():
    result = complexity_router_node(_state(uploaded_doc_text="劳动合同全文……"))

    assert result["complexity_level"] == "complex"
    assert result["execution_mode"] == "plan"
    assert result["needs_case_retrieval"] is False


# ─── 路由 ────────────────────────────────────────────────────────────────


def test_simple_turns_go_straight_to_the_supervisor():
    assert should_after_complexity(_state(execution_mode="simple")) == "supervisor"


def test_plan_turns_go_to_the_planner():
    assert should_after_complexity(_state(execution_mode="plan")) == "planner"
    # 缺字段时保守走 Plan-and-Execute，不要凭空跳过规划。
    assert should_after_complexity(_state()) == "planner"


def test_finalized_turn_goes_through_the_planner_to_clear_the_stale_plan():
    assert should_after_complexity(
        _state(execution_mode="simple", supervisor_finalized=True)
    ) == "planner"
