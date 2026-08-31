"""Statute Retrieval Agent."""
from __future__ import annotations

import json
import os
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.node_utils import compatibility_dependency, latest_human_message, record_trace_event
from agent.prompts import STATUTE_RETRIEVAL_SYSTEM_PROMPT
from agent.reports import build_agent_report
from agent.state import AgentState, StatuteReport
from agent.tool_loop import apply_tool_call_budget
from agent.tools import STATUTE_RETRIEVAL_TOOLS
from services.llm import get_llm, supports_tools


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
    tokens = re.findall(r"[\u4e00-\u9fff]{2,8}", query)
    stop = {"怎么办", "可以要求", "有没有", "是否可以", "怎么处理"}
    return [item for item in dict.fromkeys(tokens) if item not in stop][:8]


def _relevance_assessment(laws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "law_name": item.get("law_name") or item.get("title") or "",
            "article_no": item.get("article_no") or "",
            "relevant": True,
            "reason": str(item.get("content") or "")[:120],
        }
        for item in laws
        if item.get("law_name") or item.get("title")
    ]


def _law_identity(item: dict[str, Any]) -> tuple[str, str]:
    return (
        str(item.get("law_name") or item.get("title") or ""),
        str(item.get("article_no") or ""),
    )


def _ground_relevant_laws(
    retrieved: list[dict[str, Any]],
    selected: Any,
    assessment: Any,
) -> list[dict[str, Any]]:
    """Accept relevance choices only for laws that were actually retrieved."""
    allowed = {_law_identity(item): item for item in retrieved}
    selected_items = selected if isinstance(selected, list) else []
    selected_keys = {_law_identity(item) for item in selected_items if isinstance(item, dict)}
    rejected_keys = {
        _law_identity(item)
        for item in assessment if isinstance(item, dict) and item.get("relevant") is False
    } if isinstance(assessment, list) else set()
    if selected_keys:
        return [item for key, item in allowed.items() if key in selected_keys and key not in rejected_keys]
    return [item for key, item in allowed.items() if key not in rejected_keys]


async def statute_retrieval_agent_node(state: AgentState) -> dict[str, Any]:
    """Extract search terms, retrieve statutes, and emit a StatuteReport."""
    query = state.get("rewritten_query") or latest_human_message(state)
    task_id = state.get("current_step") or state.get("trace_id") or "current-request:statute_retrieval_agent"
    context = {
        "task_id": task_id,
        "query": query,
        "plan_step": next(
            (step for step in state.get("plan", []) if step.get("step_id") == state.get("current_step")),
            None,
        ),
        "case_analysis_report": next(
            (
                report for report in reversed(state.get("agent_reports", []) or [])
                if (report.get("agent_name") or report.get("agent")) == "case_analysis_agent"
            ),
            None,
        ),
        "retrieved_laws": state.get("retrieved_laws", []) or [],
    }
    llm_factory = compatibility_dependency("get_llm", get_llm)
    llm = llm_factory(
        provider=os.getenv("STATUTE_RETRIEVAL_AGENT_PROVIDER", "deepseek"),
        model=os.getenv("STATUTE_RETRIEVAL_AGENT_MODEL", "deepseek-v4-flash-vision-exp"),
        model_route="statute_retrieval_agent",
        trace_id=state.get("trace_id"),
        thread_id=state.get("thread_id"),
        temperature=0,
        streaming=False,
    )
    tool_support = compatibility_dependency("supports_tools", supports_tools)
    if tool_support() and hasattr(llm, "bind_tools"):
        tools = list(STATUTE_RETRIEVAL_TOOLS)
        if state.get("retrieved_laws"):
            tools = [tool for tool in tools if tool.name != "retrieve_local_law_tool"]
        llm = llm.bind_tools(tools)
    response = await llm.ainvoke([
        SystemMessage(content=STATUTE_RETRIEVAL_SYSTEM_PROMPT),
        *list(state.get("messages", [])),
        HumanMessage(content=json.dumps(context, ensure_ascii=False)),
    ])
    if getattr(response, "tool_calls", None):
        response, tool_call_count, failure = apply_tool_call_budget(
            response,
            state,
            agent_name="statute_retrieval_agent",
        )
        return {
            "messages": [response],
            "tool_call_count": tool_call_count,
            "tool_loop_failure": failure,
        }

    parsed = _extract_json(response.content or "") or {}
    parsed_findings = parsed.get("findings")
    if not isinstance(parsed_findings, dict):
        parsed_findings = {}
    retrieved_laws = list(state.get("retrieved_laws", []) or [])
    keywords = parsed_findings.get("keywords") or parsed.get("keywords") or _fallback_keywords(query)
    assessment = parsed_findings.get("relevance_assessment") or parsed.get("relevance_assessment") or _relevance_assessment(retrieved_laws)
    if not isinstance(keywords, list):
        keywords = _fallback_keywords(query)
    if not isinstance(assessment, list):
        assessment = _relevance_assessment(retrieved_laws)
    laws = _ground_relevant_laws(
        retrieved_laws,
        parsed_findings.get("statutes") or parsed.get("statutes"),
        assessment,
    )
    evidence_insufficient = not laws or bool(state.get("evidence_insufficient", False))
    findings = {
        "query": query,
        "keywords": keywords,
        "statutes": laws,
        "relevance_assessment": assessment,
        "evidence_insufficient": evidence_insufficient,
    }
    report: StatuteReport = build_agent_report(
        state,
        "statute_retrieval_agent",
        summary=str(parsed.get("summary") or (
            f"检索并筛选出 {len(laws)} 条相关法规依据" if laws else "未检索到充分的相关法规依据"
        )),
        findings=findings,
        sources=laws,
        confidence=str(parsed.get("confidence") or ("medium" if laws else "low")),
        status="report_ready",
        query=query,
        keywords=list(keywords),
        statutes=laws,
        relevance_assessment=list(assessment),
        evidence_insufficient=evidence_insufficient,
    )
    record_trace_event(
        state.get("trace_id"),
        "agent_report",
        name="statute_retrieval_agent",
        payload={"law_count": len(laws), "evidence_insufficient": evidence_insufficient},
    )
    return {"agent_reports": [report]}
