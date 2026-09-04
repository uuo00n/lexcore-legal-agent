"""Fact Analysis Agent 的事实充分性闸门（§四、§七、§八、§三十 用例 1、用例 2）。

覆盖四件事：
1. 简单问题不追问，也不为此多付一次模型调用（§二十六 延迟目标）；
2. 个案结论请求在事实不足时必须阻断补问，并给出可直接回答的问题；
3. 模型不可用时退回确定性判定，不抛异常、不改变阻断结论；
4. 模型不得推翻确定性的阻断判定——既不能豁免必须补问的请求，
   也不能把通用说明升级成硬性追问（§十四 同一分工口径）。
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage

from agent.agents.fact_analysis_agent import FactAnalysisOutput, fact_analysis_agent_node

SIMPLE_WAGE_QUESTION = "公司拖欠我三个月工资怎么办"
INDIVIDUAL_CONCLUSION_QUESTION = "公司辞退我，我能赔多少钱？"


class _StructuredLLM:
    """只提供结构化输出的假模型；``calls`` 用来断言是否真的调用了模型。"""

    def __init__(self, payload: dict | None = None, *, error: Exception | None = None):
        self.payload = payload or {}
        self.error = error
        self.calls = 0

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return FactAnalysisOutput.model_validate(self.payload)


def _state(question: str, **overrides) -> dict:
    state = {
        "messages": [HumanMessage(content=question)],
        "thread_id": "thread-fact",
        "trace_id": "",
    }
    state.update(overrides)
    return state


async def test_simple_question_skips_the_model_and_the_clarification_node(monkeypatch):
    llm = _StructuredLLM()
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: llm)

    result = await fact_analysis_agent_node(_state(SIMPLE_WAGE_QUESTION))

    # 确定性检查已认为事实充分，就不该再为抽取多付一次调用（§二 问题 1）。
    assert llm.calls == 0
    assert result["facts_sufficient"] is True
    assert result["needs_clarification"] is False
    assert result["clarification_blocking"] is False
    assert result["case_facts"]["source"] == "deterministic"
    assert result["case_facts"]["category"] == "labor"


async def test_individual_conclusion_request_blocks_and_asks_answerable_questions(monkeypatch):
    llm = _StructuredLLM({
        "legal_relationship": "劳动合同关系",
        "facts": ["用户被公司辞退"],
        "legal_issues": ["辞退是否合法", "赔偿标准如何计算"],
        "missing_facts": ["工作年限"],
        "facts_sufficient": False,
        "needs_clarification": True,
        "clarification_questions": ["你在这家公司工作了多久？", "你的月工资是多少？"],
    })
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: llm)

    result = await fact_analysis_agent_node(_state(INDIVIDUAL_CONCLUSION_QUESTION))

    assert llm.calls == 1
    assert result["facts_sufficient"] is False
    assert result["needs_clarification"] is True
    assert result["clarification_blocking"] is True
    assert result["needs_follow_up"] is True
    assert result["clarification_questions"] == ["你在这家公司工作了多久？", "你的月工资是多少？"]
    facts = result["case_facts"]
    assert facts["source"] == "merged"
    assert facts["legal_relationship"] == "劳动合同关系"
    # 事实分析不得凭记忆写法条：产物里根本没有引用字段（§四）。
    assert "law_name" not in facts and "citations" not in facts


async def test_model_failure_falls_back_to_the_deterministic_gate(monkeypatch):
    llm = _StructuredLLM(error=RuntimeError("provider unavailable"))
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: llm)

    result = await fact_analysis_agent_node(_state(INDIVIDUAL_CONCLUSION_QUESTION))

    assert llm.calls == 1
    assert result["case_facts"]["source"] == "deterministic"
    # 模型挂掉不改变阻断结论，也不允许把异常抛给用户（§P1-5 同口径）。
    assert result["needs_clarification"] is True
    assert result["clarification_blocking"] is True
    assert result["clarification_questions"]


async def test_model_cannot_exempt_a_request_that_must_be_clarified_first(monkeypatch):
    llm = _StructuredLLM({
        "legal_relationship": "劳动合同关系",
        "facts": [],
        "legal_issues": [],
        "missing_facts": [],
        "facts_sufficient": True,
        "needs_clarification": False,
        "clarification_questions": [],
    })
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: llm)

    result = await fact_analysis_agent_node(_state(INDIVIDUAL_CONCLUSION_QUESTION))

    assert result["facts_sufficient"] is False
    assert result["needs_clarification"] is True
    assert result["clarification_blocking"] is True
    # 模型没给问题时用确定性模板兜底，不能出现「要补问但没有问题」的状态。
    assert result["clarification_questions"]


async def test_uploaded_document_is_read_before_asking_the_user(monkeypatch):
    llm = _StructuredLLM()
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: llm)

    result = await fact_analysis_agent_node(
        _state(INDIVIDUAL_CONCLUSION_QUESTION, uploaded_doc_text="劳动合同全文……")
    )

    assert llm.calls == 0
    assert result["needs_clarification"] is False
    assert result["clarification_blocking"] is False


async def test_finalized_turn_skips_fact_analysis_entirely(monkeypatch):
    llm = _StructuredLLM()
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: llm)

    result = await fact_analysis_agent_node(
        _state("今天天气不错", supervisor_finalized=True)
    )

    assert result == {}
    assert llm.calls == 0


async def test_merged_question_drives_the_resume_turn(monkeypatch):
    llm = _StructuredLLM()
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: llm)

    result = await fact_analysis_agent_node(
        _state(
            "3 年，月薪 8000",
            clarification_resumed=True,
            clarification_round=1,
            rewritten_query=(
                "公司辞退我，我能赔多少钱？ 我在公司干了3年，月薪8000元，"
                "上个月被辞退，有劳动合同和工资流水"
            ),
        )
    )

    # 用合并后的问题判定，事实已经补足；只看「3 年，月薪 8000」会被判成新的欠薪问题。
    assert result["facts_sufficient"] is True
    assert result["needs_clarification"] is False
    assert result["clarification_blocking"] is False
    assert llm.calls == 0


async def test_case_analysis_agent_no_longer_re_asks_after_the_gate(monkeypatch):
    from agent.agents.case_analysis_agent import case_analysis_agent_node

    class _CaseLLM:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            from langchain_core.messages import AIMessage

            return AIMessage(content='{"summary": "已整理辞退争议", "status": "facts_sufficient"}')

    llm = _CaseLLM()
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: llm)
    monkeypatch.setattr("agent.nodes.supports_tools", lambda provider=None: False)

    result = await case_analysis_agent_node(
        _state(
            INDIVIDUAL_CONCLUSION_QUESTION,
            case_facts={"facts_sufficient": True, "source": "deterministic"},
            facts_sufficient=True,
        )
    )

    # 闸门已经判过事实充分性，计划执行到一半不得再改判「要问用户」（§八）。
    assert result["needs_follow_up"] is False
    assert result["agent_reports"][0]["status"] != "needs_more_facts"
