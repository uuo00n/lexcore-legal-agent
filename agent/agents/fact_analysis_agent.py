"""Fact Analysis Agent：正式规划之前的事实充分性闸门（§四、§七、§十五）。

职责边界（§四）：
- 只整理事实、法律关系、争议焦点与事实缺口；
- **不检索法规、不检索类案、不凭记忆引用法条**，因此这里不绑定任何工具；
- 不给结论、不给金额、不给胜负判断。

延迟取舍：事实是否充分先由 ``services.legal_analysis`` 的确定性检查判定，只有在
确定性检查认为事实不足（也就是真的可能要追问）时才额外调用一次模型做抽取。
简单问题（如「公司拖欠我三个月工资怎么办」）因此不会多付一次模型调用，
这正是 §二 问题 1 与 §二十六 延迟目标所要求的。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.clarification import (
    ClarificationDecision,
    MAX_CLARIFICATION_QUESTIONS,
    clarification_round_count,
    decide_clarification,
)
from agent.node_utils import compatibility_dependency, effective_question, record_trace_event
from agent.prompts import FACT_ANALYSIS_SYSTEM_PROMPT
from agent.state import AgentState, CaseFacts
from services.context_builder import build_model_context
from services.llm import get_llm
from services.model_defaults import FAST, resolve_model, resolve_provider

# 事实缺口列表上限：进入 Planner 与答案的「需要补充的信息」，过长反而没人读。
_MAX_MISSING_FACTS = 6


class FactAnalysisOutput(BaseModel):
    """Fact Analysis Agent 的结构化产物；不含任何法条或案号字段（§四）。"""

    model_config = ConfigDict(extra="forbid")

    legal_relationship: str = Field(default="", max_length=200)
    facts: list[str] = Field(default_factory=list, max_length=12)
    legal_issues: list[str] = Field(default_factory=list, max_length=8)
    missing_facts: list[str] = Field(default_factory=list, max_length=8)
    facts_sufficient: bool = False
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(
        default_factory=list,
        max_length=MAX_CLARIFICATION_QUESTIONS,
    )


def _clean_list(values: Any, *, limit: int) -> list[str]:
    cleaned: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text and text not in cleaned:
            cleaned.append(text[:200])
        if len(cleaned) >= limit:
            break
    return cleaned


async def _extract_facts(
    state: AgentState,
    question: str,
    decision: ClarificationDecision,
) -> FactAnalysisOutput | None:
    """调用模型抽取结构化事实；模型不可用时返回 ``None`` 走确定性兜底（§P1-5 同口径）。"""
    task_context = {
        "用户问题": question,
        "已确认事实": state.get("confirmed_facts") or {},
        "缺失事实维度": decision.missing_facts,
        "补问轮次": clarification_round_count(state),
    }
    try:
        llm_factory = compatibility_dependency("get_llm", get_llm)
        llm = llm_factory(
            provider=resolve_provider(
                "FACT_ANALYSIS_AGENT_PROVIDER", "FACT_AGENT_PROVIDER", tier=FAST
            ),
            model=resolve_model("FACT_ANALYSIS_AGENT_MODEL", "FACT_AGENT_MODEL", tier=FAST),
            model_route="fact_analysis_agent",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0,
            streaming=False,
        )
        built = build_model_context(state, FACT_ANALYSIS_SYSTEM_PROMPT, task_context=task_context)
        # 事实分析禁止检索，因此这里不 bind_tools；结构化输出保证字段可直接入 State。
        structured_llm = llm.with_structured_output(FactAnalysisOutput)
        raw = await structured_llm.ainvoke(built.messages)
        return raw if isinstance(raw, FactAnalysisOutput) else FactAnalysisOutput.model_validate(raw)
    except Exception as exc:
        record_trace_event(
            state.get("trace_id"),
            "agent_fallback",
            name="fact_analysis_agent",
            payload={"error": str(exc)},
        )
        return None


def _build_case_facts(
    decision: ClarificationDecision,
    extracted: FactAnalysisOutput | None,
    *,
    missing: list[str],
    facts_sufficient: bool,
    needs_clarification: bool,
    questions: list[str],
) -> CaseFacts:
    facts: CaseFacts = {
        "legal_relationship": extracted.legal_relationship.strip() if extracted else "",
        "facts": _clean_list(extracted.facts if extracted else [], limit=12),
        "legal_issues": _clean_list(extracted.legal_issues if extracted else [], limit=8),
        "missing_facts": missing,
        "facts_sufficient": facts_sufficient,
        "needs_clarification": needs_clarification,
        "clarification_questions": questions,
        "category": decision.category,
        "missing_dimensions": list(decision.missing_facts),
        "source": "merged" if extracted else "deterministic",
    }
    return facts


async def fact_analysis_agent_node(state: AgentState) -> dict[str, Any]:
    """判断事实是否足以给出个案结论，必要时准备补问内容（§七、§八）。"""
    if state.get("supervisor_finalized"):
        # 非法律闲聊等场景已经由 Intent Router 生成最终答复，不再做事实分析。
        return {}

    # 澄清恢复轮次要用 Fact Merge 合并后的完整问题重新判定，否则「3 年」这类补充
    # 会被当成新问题，导致补问永远收敛不了（§八）。
    question = effective_question(state)
    decision = decide_clarification(
        question,
        confirmed_facts=state.get("confirmed_facts") or {},
        has_uploaded_doc=bool(state.get("uploaded_doc_text")),
        round_count=clarification_round_count(state),
    )

    extracted: FactAnalysisOutput | None = None
    if question and decision.is_legal and not decision.facts_sufficient:
        extracted = await _extract_facts(state, question, decision)

    missing = _clean_list(
        [*decision.missing_facts, *(extracted.missing_facts if extracted else [])],
        limit=_MAX_MISSING_FACTS,
    )
    facts_sufficient = decision.facts_sufficient and (extracted.facts_sufficient if extracted else True)
    # 阻断与否只由确定性规则决定：模型既不能把通用咨询升级成硬性追问，
    # 也不能豁免一个必须先补问的个案结论请求（§十四 同一分工口径）。
    blocking = decision.blocking
    needs_clarification = decision.needs_clarification or bool(
        extracted and extracted.needs_clarification and not decision.facts_sufficient
    )
    if decision.reason in {"not_legal", "uploaded_doc", "clarification_budget_exhausted"}:
        needs_clarification = False
        blocking = False

    questions: list[str] = []
    if needs_clarification:
        questions = _clean_list(
            extracted.clarification_questions if extracted else [],
            limit=MAX_CLARIFICATION_QUESTIONS,
        ) or list(decision.questions)

    result: dict[str, Any] = {
        "case_facts": _build_case_facts(
            decision,
            extracted,
            missing=missing,
            facts_sufficient=facts_sufficient,
            needs_clarification=needs_clarification,
            questions=questions,
        ),
        "missing_facts": missing,
        "facts_sufficient": facts_sufficient,
        "needs_clarification": needs_clarification,
        "clarification_blocking": blocking and needs_clarification,
        "clarification_questions": questions,
        # 兼容既有字段：旧链路用 needs_follow_up 表示「还要问用户」。
        "needs_follow_up": needs_clarification,
    }
    record_trace_event(
        state.get("trace_id"),
        "fact_analysis",
        name="fact_analysis_agent",
        payload={
            "reason": decision.reason,
            "category": decision.category,
            "facts_sufficient": facts_sufficient,
            "needs_clarification": needs_clarification,
            "clarification_blocking": result["clarification_blocking"],
            "missing_facts": missing,
            "llm_used": extracted is not None,
        },
    )
    return result


def case_facts_payload(state: AgentState) -> dict[str, Any]:
    """给下游节点（Planner / Answer Generator）读取事实的统一入口。"""
    facts = state.get("case_facts") or {}
    return {
        "结构化事实": facts,
        "已确认事实": state.get("confirmed_facts") or {},
        "待补充事实": list(state.get("missing_facts", []) or []),
        "事实是否充分": bool(state.get("facts_sufficient", True)),
    }


__all__ = [
    "FactAnalysisOutput",
    "case_facts_payload",
    "fact_analysis_agent_node",
]
