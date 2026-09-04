"""Case Retrieval Agent（§五）：按需检索裁判案例的独立执行单元。

原实现把 ``case_retrieval`` 任务分派给 ``case_analysis_agent``，等于让事实分析 Agent
兼职查类案：既无法在计划里表达「本轮不需要类案」，也无法在 Repair Router 里只重跑
类案检索。本模块把这项职责单独拆出来——只绑定 ``search_case_tool``，只产出案例证据，
不整理事实、不检索法规、不给法律结论。
"""
from __future__ import annotations

import json
import re
from typing import Any

from agent.agent_names import CASE_RETRIEVAL_AGENT, FACT_ANALYSIS_AGENT, same_agent
from agent.node_utils import compatibility_dependency, latest_human_message, record_trace_event
from agent.prompts import CASE_RETRIEVAL_SYSTEM_PROMPT
from agent.reports import build_agent_report, report_agent_name
from agent.state import AgentState, CaseRetrievalReport
from agent.tool_loop import admit_tool_calls
from agent.tools import CASE_RETRIEVAL_TOOLS
from services.context_builder import build_model_context
from services.llm import get_llm, supports_tools
from services.model_defaults import STRONG, resolve_model, resolve_provider


def _extract_json(content: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", content or "", flags=re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _fallback_keywords(query: str) -> list[str]:
    tokens = re.findall(r"[一-鿿]{2,8}", query)
    stop = {"怎么办", "可以要求", "有没有", "是否可以", "怎么处理", "类似案例", "判例"}
    return [item for item in dict.fromkeys(tokens) if item not in stop][:8]


def _case_identity(item: dict[str, Any]) -> tuple[str, str]:
    return (
        str(item.get("case_no") or ""),
        str(item.get("case_name") or item.get("title") or ""),
    )


def _relevance_assessment(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "case_name": item.get("case_name") or item.get("title") or "",
            "case_no": item.get("case_no") or "",
            "relevant": True,
            "reason": str(item.get("dispute_focus") or item.get("summary") or "")[:120],
        }
        for item in cases
        if item.get("case_no") or item.get("case_name") or item.get("title")
    ]


def _ground_relevant_cases(
    retrieved: list[dict[str, Any]],
    selected: Any,
    assessment: Any,
) -> list[dict[str, Any]]:
    """只接受本轮真的检索到的案例；模型另行写出的案号一律丢弃。"""
    allowed = {_case_identity(item): item for item in retrieved}
    selected_items = selected if isinstance(selected, list) else []
    selected_keys = {_case_identity(item) for item in selected_items if isinstance(item, dict)}
    rejected_keys = {
        _case_identity(item)
        for item in assessment
        if isinstance(item, dict) and item.get("relevant") is False
    } if isinstance(assessment, list) else set()
    if selected_keys:
        return [item for key, item in allowed.items() if key in selected_keys and key not in rejected_keys]
    return [item for key, item in allowed.items() if key not in rejected_keys]


def _fact_report(state: AgentState) -> dict[str, Any] | None:
    for report in reversed(state.get("agent_reports", []) or []):
        if same_agent(report_agent_name(report), FACT_ANALYSIS_AGENT):
            return report
    return None


async def case_retrieval_agent_node(state: AgentState) -> dict[str, Any]:
    """检索与争议焦点相近的裁判案例，并提交只含本轮检索结果的类案报告。"""
    query = state.get("rewritten_query") or latest_human_message(state)
    task_id = (
        state.get("current_step")
        or state.get("trace_id")
        or f"current-request:{CASE_RETRIEVAL_AGENT}"
    )
    context = {
        "task_id": task_id,
        "query": query,
        "plan_step": next(
            (
                step
                for step in state.get("plan", [])
                if step.get("step_id") == state.get("current_step")
            ),
            None,
        ),
        "case_analysis_report": _fact_report(state),
        "retrieved_cases": state.get("retrieved_cases", []) or [],
    }
    llm_factory = compatibility_dependency("get_llm", get_llm)
    llm = llm_factory(
        provider=resolve_provider("CASE_RETRIEVAL_AGENT_PROVIDER", tier=STRONG),
        model=resolve_model("CASE_RETRIEVAL_AGENT_MODEL", tier=STRONG),
        model_route=CASE_RETRIEVAL_AGENT,
        trace_id=state.get("trace_id"),
        thread_id=state.get("thread_id"),
        temperature=0,
        streaming=False,
    )
    tool_support = compatibility_dependency("supports_tools", supports_tools)
    if tool_support() and hasattr(llm, "bind_tools"):
        llm = llm.bind_tools(list(CASE_RETRIEVAL_TOOLS))
    built = build_model_context(state, CASE_RETRIEVAL_SYSTEM_PROMPT, task_context=context)
    record_trace_event(
        state.get("trace_id"),
        "context_build",
        name=CASE_RETRIEVAL_AGENT,
        payload=built.status,
    )
    response = await llm.ainvoke(built.messages)
    step = admit_tool_calls(response, state, agent_name=CASE_RETRIEVAL_AGENT)
    if step.continue_loop:
        return {**step.updates, "context_build_status": built.status}
    # 软停止（证据够用 / 重复检索 / 零增益）时不再调用工具，直接用已有案例写报告（§P1-2、§P1-3）。
    parsed = _extract_json(response.content or "") or {}
    parsed_findings = parsed.get("findings")
    if not isinstance(parsed_findings, dict):
        parsed_findings = {}
    retrieved_cases = list(state.get("retrieved_cases", []) or [])
    keywords = parsed_findings.get("keywords") or parsed.get("keywords") or _fallback_keywords(query)
    assessment = (
        parsed_findings.get("relevance_assessment")
        or parsed.get("relevance_assessment")
        or _relevance_assessment(retrieved_cases)
    )
    if not isinstance(keywords, list):
        keywords = _fallback_keywords(query)
    if not isinstance(assessment, list):
        assessment = _relevance_assessment(retrieved_cases)
    cases = _ground_relevant_cases(
        retrieved_cases,
        parsed_findings.get("cases") or parsed.get("cases"),
        assessment,
    )
    evidence_insufficient = not cases
    findings = {
        "query": query,
        "keywords": keywords,
        "cases": cases,
        "relevance_assessment": assessment,
        "evidence_insufficient": evidence_insufficient,
    }
    report: CaseRetrievalReport = build_agent_report(
        state,
        CASE_RETRIEVAL_AGENT,
        summary=str(parsed.get("summary") or (
            f"检索并筛选出 {len(cases)} 篇相近裁判案例" if cases else "未检索到足够相近的裁判案例"
        )),
        findings=findings,
        sources=cases,
        confidence=str(parsed.get("confidence") or ("medium" if cases else "low")),
        status="report_ready",
        query=query,
        keywords=list(keywords),
        cases=cases,
        relevance_assessment=list(assessment),
        evidence_insufficient=evidence_insufficient,
    )
    record_trace_event(
        state.get("trace_id"),
        "agent_report",
        name=CASE_RETRIEVAL_AGENT,
        payload={"case_count": len(cases), "evidence_insufficient": evidence_insufficient},
    )
    return {"agent_reports": [report], "context_build_status": built.status}


__all__ = ["case_retrieval_agent_node"]
