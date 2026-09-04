"""Case Analysis Agent implementation."""
from __future__ import annotations

import json
from typing import Any

from agent.node_utils import compatibility_dependency, effective_question, record_trace_event
from agent.prompts import CASE_ANALYSIS_SYSTEM_PROMPT
from agent.reports import build_agent_report
from agent.state import AgentState
from agent.tool_loop import admit_tool_calls
from agent.tools import CASE_ANALYSIS_TOOLS
from services.answer_format import strip_answer_markdown
from services.context_builder import build_model_context
from services.legal_analysis import build_follow_up_response, classify_legal_intent, should_ask_follow_up
from services.llm import get_llm, supports_tools
from services.model_defaults import FAST, STRONG, resolve_model, resolve_provider


def _extract_json(content: str) -> dict[str, Any] | None:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _case_sources(state: AgentState) -> list[dict[str, Any]]:
    return [
        *list(state.get("retrieved_cases", []) or []),
        *list(state.get("retrieved_laws", []) or []),
    ]


def _case_report(
    state: AgentState,
    *,
    status: str,
    summary: str,
    findings: dict[str, Any],
    confidence: str = "medium",
    **extra: Any,
) -> dict[str, Any]:
    return build_agent_report(
        state,
        "case_analysis_agent",
        summary=summary,
        findings=findings,
        sources=_case_sources(state),
        confidence=confidence,
        status=status,
        **extra,
    )


async def _llm_case_follow_up(
    state: AgentState,
    latest_query: str,
    decision: dict[str, Any],
) -> str:
    fallback = build_follow_up_response(latest_query)
    try:
        llm_factory = compatibility_dependency("get_llm", get_llm)
        llm = llm_factory(
            provider=resolve_provider(
                "CASE_ANALYSIS_AGENT_PROVIDER", "FACT_AGENT_PROVIDER", tier=FAST
            ),
            model=resolve_model("CASE_ANALYSIS_AGENT_MODEL", "FACT_AGENT_MODEL", tier=FAST),
            model_route="case_analysis_agent",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0.2,
            streaming=False,
        )
        built = build_model_context(
            state,
            "你是案件分析智能体。只生成 1-3 个补齐关键事实的问题，不给法律结论。",
            task_context={
                "用户问题": latest_query,
                "缺失维度": decision.get("facts", {}).get("missing_dimensions", []),
                "建议问题": decision.get("questions", []),
            },
        )
        response = await llm.ainvoke(built.messages)
        return (response.content or "").strip() or fallback
    except Exception as exc:
        record_trace_event(state.get("trace_id"), "agent_fallback", name="case_analysis_agent", payload={"error": str(exc)})
        return fallback


async def case_analysis_agent_node(state: AgentState) -> dict[str, Any]:
    """Extract and structure a case without taking over final consultation."""
    latest_query = effective_question(state)
    if not latest_query:
        return {"needs_follow_up": False}

    if not classify_legal_intent(latest_query).get("is_legal"):
        report = _case_report(
            state,
            status="non_legal",
            summary="未识别到需要案件分析的法律事实",
            findings={
                "facts": [], "timeline": [], "parties": [], "legal_relationships": [],
                "disputed_issues": [], "claims_and_defenses": [], "evidence_gaps": [],
                "suggested_questions": [],
            },
            confidence="high",
        )
        return {"needs_follow_up": False, "agent_reports": [report]}

    # 事实充分性已经由计划之前的 Fact Analysis 闸门判定过（§七、§八）：真正需要补问的
    # 请求根本不会走到这里。计划执行到一半再改判「要问用户」会把澄清循环和执行链路搅在
    # 一起，所以闸门跑过之后本节点只做结构化分析。没有 case_facts 说明是旧链路或直接
    # 调用本节点的调用方，保留原有行为。
    gate_already_ran = state.get("case_facts") is not None
    decision: dict[str, Any] = (
        {"should_ask": False}
        if gate_already_ran
        else should_ask_follow_up(latest_query, has_uploaded_doc=bool(state.get("uploaded_doc_text")))
    )
    if decision["should_ask"]:
        response = await _llm_case_follow_up(state, latest_query, decision)
        questions = decision.get("questions", [])
        findings = {
            "facts": [],
            "timeline": [],
            "parties": [],
            "legal_relationships": [],
            "disputed_issues": [],
            "claims_and_defenses": [],
            "evidence_gaps": decision.get("facts", {}).get("missing_dimensions", []),
            "suggested_questions": questions,
        }
        report = _case_report(
            state,
            status="needs_more_facts",
            summary=decision.get("reason", "关键事实不足，需要补充"),
            findings=findings,
            suggested_questions=questions,
            missing_facts=findings["evidence_gaps"],
            draft_response=response,
        )
        return {"needs_follow_up": True, "agent_reports": [report]}

    context = {
        "task_id": state.get("current_step") or state.get("trace_id") or "current-request:case_analysis_agent",
        "query": latest_query,
        # 上传文档由 build_model_context 统一按档位预算注入证据区，这里不再重复裁剪。
        "existing_reports": state.get("agent_reports", []) or [],
    }
    llm_factory = compatibility_dependency("get_llm", get_llm)
    llm = llm_factory(
        provider=resolve_provider(
            "CASE_ANALYSIS_AGENT_PROVIDER", "FACT_AGENT_PROVIDER", tier=STRONG
        ),
        model=resolve_model("CASE_ANALYSIS_AGENT_MODEL", "FACT_AGENT_MODEL", tier=STRONG),
        model_route="case_analysis_agent",
        trace_id=state.get("trace_id"),
        thread_id=state.get("thread_id"),
        temperature=0.1,
        streaming=False,
    )
    tool_support = compatibility_dependency("supports_tools", supports_tools)
    if tool_support() and hasattr(llm, "bind_tools"):
        llm = llm.bind_tools(CASE_ANALYSIS_TOOLS)
    built = build_model_context(state, CASE_ANALYSIS_SYSTEM_PROMPT, task_context=context)
    record_trace_event(
        state.get("trace_id"),
        "context_build",
        name="case_analysis_agent",
        payload=built.status,
    )
    response = await llm.ainvoke(built.messages)
    step = admit_tool_calls(response, state, agent_name="case_analysis_agent")
    if step.continue_loop:
        return {**step.updates, "context_build_status": built.status}
    # 软停止时直接用已有证据整理事实与争议结构（§P1-2、§P1-3）。
    parsed = _extract_json(response.content or "") or {}
    findings = parsed.get("findings")
    if not isinstance(findings, dict):
        findings = {
            "facts": [], "timeline": [], "parties": [], "legal_relationships": [],
            "disputed_issues": [], "claims_and_defenses": [], "evidence_gaps": [],
            "analysis_note": strip_answer_markdown(response.content or ""),
        }
    report = _case_report(
        state,
        status=str(parsed.get("status") or "facts_sufficient"),
        summary=str(parsed.get("summary") or "案件事实与争议结构已整理"),
        findings=findings,
        confidence=str(parsed.get("confidence") or "medium"),
    )
    record_trace_event(state.get("trace_id"), "agent_report", name="case_analysis_agent", payload={"status": report["status"]})
    return {
        "needs_follow_up": False,
        "agent_reports": [report],
        "context_build_status": built.status,
    }
