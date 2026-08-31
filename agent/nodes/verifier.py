"""Plan result verification and final response generation."""
from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.agents.legal_consult_agent import _guard_law_citations
from agent.node_utils import compatibility_dependency, latest_human_message, record_trace_event
from agent.prompts import VERIFIER_FINAL_PROMPT
from agent.reports import report_agent_name
from agent.state import AgentState, VerificationResult
from services.answer_format import strip_answer_markdown
from services.legal_analysis import score_legal_answer
from services.llm import get_llm


def _last_report_from(state: AgentState, agent_name: str) -> dict[str, Any] | None:
    for report in reversed(state.get("agent_reports", []) or []):
        if report_agent_name(report) == agent_name:
            return report
    return None


def _fallback_final_response(state: AgentState) -> str:
    reports = state.get("agent_reports", []) or []
    latest = reports[-1] if reports else {}
    findings = latest.get("findings") if isinstance(latest.get("findings"), dict) else {}
    questions = (
        latest.get("suggested_questions")
        or latest.get("questions")
        or findings.get("suggested_questions")
        or []
    )
    if latest.get("status") == "needs_more_facts" and questions:
        lines = "\n".join(
            f"{index}. {question}"
            for index, question in enumerate(questions[:3], start=1)
        )
        return f"我还需要先确认几个关键信息：\n{lines}"
    for key in ("draft_response", "final_response", "analysis", "summary"):
        value = latest.get(key)
        if isinstance(value, str) and value.strip():
            return strip_answer_markdown(value)
    if questions:
        lines = "\n".join(
            f"{index}. {question}"
            for index, question in enumerate(questions[:3], start=1)
        )
        return f"我还需要先确认几个关键信息：\n{lines}"
    return "本轮专业步骤已经执行完毕，但可供核验的专家结论不足，请补充信息后重试。"


def _trusted_laws_for_final(state: AgentState) -> list[dict[str, Any]]:
    statute_report = _last_report_from(state, "statute_retrieval_agent")
    if statute_report is not None:
        return [
            item
            for item in statute_report.get("sources", []) or []
            if isinstance(item, dict)
        ]
    return list(state.get("retrieved_laws", []) or [])


def verify_plan_results(state: AgentState) -> VerificationResult:
    """Verify that every planned step reached a report-backed terminal state."""
    plan = state.get("plan", []) or []
    issues: list[str] = []
    completed = 0
    for step in plan:
        step_id = str(step.get("step_id") or "")
        status = step.get("status")
        if status == "completed":
            completed += 1
            if not step.get("result"):
                issues.append(f"{step_id or 'unknown_step'} 缺少执行结果")
        elif status == "failed":
            issues.append(f"{step_id or 'unknown_step'} 执行失败")
        else:
            issues.append(f"{step_id or 'unknown_step'} 尚未完成")

    report_task_ids = {
        str(report.get("task_id") or report.get("step_id") or "")
        for report in state.get("agent_reports", []) or []
    }
    for step in plan:
        step_id = str(step.get("step_id") or "")
        if step.get("status") == "completed" and step_id and step_id not in report_task_ids:
            # Legacy reports may omit task_id; step.result is still an auditable copy.
            if not isinstance(step.get("result"), dict):
                issues.append(f"{step_id} 缺少对应的专家报告")

    score = completed / len(plan) if plan else 0.0
    passed = bool(plan) and not issues and completed == len(plan)
    return {
        "passed": passed,
        "score": round(score, 4),
        "issues": issues,
        "reason": "所有计划步骤均有专家报告" if passed else "；".join(issues) or "计划为空",
    }


def _citations_from_reports(state: AgentState) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for report in state.get("agent_reports", []) or []:
        for source in report.get("sources", []) or []:
            if not isinstance(source, dict):
                continue
            source_type = str(source.get("source_type") or "")
            source_id = str(source.get("source_id") or source.get("case_id") or "")
            title = str(source.get("title") or source.get("law_name") or source.get("case_name") or "")
            article_no = str(source.get("article_no") or "")
            key = (source_type, source_id, title, article_no)
            if key in seen:
                continue
            seen.add(key)
            citations.append({
                "citation_id": f"citation_{len(citations) + 1}",
                "source_type": source_type,
                "source_id": source_id,
                "title": title,
                "article_no": article_no,
                "content": str(source.get("content") or source.get("summary") or ""),
                "url": str(source.get("url") or ""),
            })
    return citations


async def _llm_verifier_final_response(state: AgentState) -> str:
    """Render a user response strictly from the verified specialist reports."""
    latest_query = latest_human_message(state)
    payload = {
        "用户问题": latest_query,
        "执行计划": state.get("plan", []) or [],
        "核验结果": verify_plan_results(state),
        "专家报告": state.get("agent_reports", []) or [],
        "检索法条": _trusted_laws_for_final(state),
        "上传文档": state.get("uploaded_doc_name") or "",
    }
    fallback = _fallback_final_response(state)
    try:
        llm_factory = compatibility_dependency("get_llm", get_llm)
        llm = llm_factory(
            provider=os.getenv("VERIFIER_PROVIDER", os.getenv("SUPERVISOR_PROVIDER", "deepseek")),
            model=os.getenv("VERIFIER_MODEL", os.getenv("SUPERVISOR_MODEL", "deepseek-v4-flash-vision-exp")),
            model_route="verifier",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0.2,
            streaming=False,
        )
        response = await llm.ainvoke([
            SystemMessage(content=VERIFIER_FINAL_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        content = strip_answer_markdown((response.content or "").strip()) or fallback
    except Exception as exc:
        record_trace_event(
            state.get("trace_id"),
            "agent_fallback",
            name="verifier",
            payload={"error": str(exc)},
        )
        content = fallback

    trusted_laws = _trusted_laws_for_final(state)
    if trusted_laws:
        content = strip_answer_markdown(_guard_law_citations(content, trusted_laws))
    return content


async def verifier_node(state: AgentState) -> dict[str, Any]:
    """Verify completed plan results and produce the report-grounded final answer."""
    verification = verify_plan_results(state)
    content = await _llm_verifier_final_response(state)
    record_trace_event(
        state.get("trace_id"),
        "verification_complete",
        name="verifier",
        payload={
            "passed": verification["passed"],
            "score": verification["score"],
            "issues": verification["issues"],
            "answer_score": score_legal_answer(
                latest_human_message(state),
                content,
                state.get("retrieved_laws", []),
            ),
        },
    )
    return {
        "verification_result": verification,
        "citations": _citations_from_reports(state),
        "supervisor_route": "end",
        "supervisor_reason": "计划执行结果已完成核验",
        "supervisor_finalized": True,
        "messages": [AIMessage(content=content)],
    }
