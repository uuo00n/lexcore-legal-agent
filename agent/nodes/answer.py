"""Generate a grounded final answer from verified graph state."""
from __future__ import annotations

import json
import os
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.agents.legal_consult_agent import _guard_law_citations
from agent.node_utils import compatibility_dependency, latest_human_message, record_trace_event
from agent.prompts import ANSWER_GENERATOR_PROMPT
from agent.state import AgentState, VerificationResult
from services.answer_format import strip_answer_markdown
from services.legal_analysis import score_legal_answer
from services.llm import get_llm

from .verifier import (
    _looks_like_case,
    _looks_like_law,
    _matches_case,
    _matches_law,
    _normalize_text,
    _source_id,
    verify_plan_results,
)


_CASE_NO_RE = re.compile(r"[（(][12]\d{3}[）)][^，。；;\s]{1,40}?号")
_ABSOLUTE_CLAIMS = {
    "一定胜诉": "存在胜诉可能，但仍取决于证据和裁判认定",
    "必然胜诉": "存在胜诉可能，但仍取决于证据和裁判认定",
    "肯定能赢": "存在胜诉可能，但仍取决于证据和裁判认定",
    "百分百胜诉": "存在胜诉可能，但仍取决于证据和裁判认定",
    "百分之百胜诉": "存在胜诉可能，但仍取决于证据和裁判认定",
}


def _original_question(state: AgentState) -> str:
    return str(state.get("original_query") or latest_human_message(state) or "")


def _trusted_laws(state: AgentState) -> list[dict[str, Any]]:
    return [dict(item) for item in state.get("retrieved_laws", []) or []]


def _trusted_cases(state: AgentState) -> list[dict[str, Any]]:
    return [dict(item) for item in state.get("retrieved_cases", []) or []]


def _report_text(report: dict[str, Any], *keys: str) -> str:
    findings = report.get("findings") if isinstance(report.get("findings"), dict) else {}
    for key in keys:
        value = report.get(key)
        if not isinstance(value, str):
            value = findings.get(key)
        if isinstance(value, str) and value.strip():
            return strip_answer_markdown(value)
    return ""


def _report_list(report: dict[str, Any], *keys: str) -> list[str]:
    findings = report.get("findings") if isinstance(report.get("findings"), dict) else {}
    for key in keys:
        value = report.get(key)
        if not isinstance(value, list):
            value = findings.get(key)
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
    return []


def _source_label(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("source_type", "source_id", "url"):
        value = str(item.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    if not parts:
        fallback = str(
            item.get("title")
            or item.get("law_name")
            or item.get("case_name")
            or item.get("case_no")
            or "本轮检索结果"
        ).strip()
        parts.append(fallback)
    return "，".join(parts)


def _law_reference_lines(laws: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in laws:
        law_name = str(item.get("law_name") or item.get("title") or "").strip()
        article_no = str(item.get("article_no") or "").strip()
        if not law_name:
            continue
        citation = f"《{law_name}》{article_no}"
        content = str(item.get("content") or "").strip()
        rule = f"：{content}" if content else ""
        lines.append(f"{citation}{rule}（来源：{_source_label(item)}）")
    return lines


def _case_reference_lines(cases: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in cases:
        case_name = str(item.get("case_name") or item.get("title") or "").strip()
        case_no = str(item.get("case_no") or "").strip()
        label = "，".join(value for value in (case_name, case_no) if value)
        if not label:
            continue
        summary = str(item.get("summary") or "").strip()
        point = f"：{summary}" if summary else ""
        lines.append(f"{label}{point}（来源：{_source_label(item)}）")
    return lines


def _risk_detail(verification: VerificationResult) -> str:
    details: list[str] = []
    for key in ("issues", "missing_sources", "invalid_citations"):
        for item in verification.get(key, []) or []:
            detail = str(item).strip()
            if detail and detail not in details:
                details.append(detail)
    return "；".join(details[:5]) or "现有材料未通过完整核验"


def _has_uncertainty(verification: VerificationResult) -> bool:
    return not verification.get("passed", False) or any(
        verification.get(key) for key in ("issues", "missing_sources", "invalid_citations")
    )


def _risk_notice(verification: VerificationResult) -> str:
    return (
        f"风险提示：结果核验存在不确定性（{_risk_detail(verification)}）。"
        "以下内容仅基于当前可核验材料，不能视为确定或完整的法律结论。"
    )


def _fallback_final_response(
    state: AgentState,
    verification: VerificationResult,
) -> str:
    reports = list(state.get("agent_reports", []) or [])
    latest = reports[-1] if reports else {}
    conclusion = _report_text(latest, "draft_response", "final_response", "summary")
    analysis = _report_text(latest, "analysis", "summary")
    questions = _report_list(latest, "suggested_questions", "questions")
    next_steps = _report_list(latest, "next_steps", "suggested_actions", "recommendations")

    if not conclusion:
        conclusion = "现有专家报告不足以形成可核验的明确结论。"
    if not analysis:
        analysis = "目前只能基于已提交的专家报告和检索材料作有限分析。"

    law_lines = _law_reference_lines(_trusted_laws(state))
    case_lines = _case_reference_lines(_trusted_cases(state))
    risk = _risk_detail(verification) if _has_uncertainty(verification) else "仍需结合完整事实、证据和有效法律文本审慎判断。"
    if questions:
        risk = f"{risk}；尚需确认：{'；'.join(questions[:3])}"

    return "\n".join(
        (
            "1. 结论",
            conclusion,
            "2. 法律分析",
            analysis,
            "3. 法律依据",
            "\n".join(law_lines) if law_lines else "本轮没有可供引用的检索法条。",
            "4. 类案参考",
            "\n".join(case_lines) if case_lines else "本轮没有可供引用的检索案例。",
            "5. 风险与不确定性",
            risk,
            "6. 建议下一步",
            "；".join(next_steps[:3]) if next_steps else "现有报告未提供可核验的下一步建议。",
        )
    )


def _guard_case_citations(content: str, cases: list[dict[str, Any]]) -> str:
    allowed = {_normalize_text(item.get("case_no")) for item in cases if item.get("case_no")}
    return _CASE_NO_RE.sub(
        lambda match: match.group(0)
        if _normalize_text(match.group(0)) in allowed
        else "（未在本轮检索结果中确认的案例引用已移除）",
        content,
    )


def _add_inline_sources(
    content: str,
    laws: list[dict[str, Any]],
    cases: list[dict[str, Any]],
) -> str:
    for item in laws:
        law_name = str(item.get("law_name") or item.get("title") or "").strip()
        article_no = str(item.get("article_no") or "").strip()
        if not law_name or not article_no:
            continue
        aliases = [law_name]
        short_name = law_name.removeprefix("中华人民共和国")
        if short_name and short_name not in aliases:
            aliases.append(short_name)
        for alias in aliases:
            citation = f"《{alias}》{article_no}"
            pattern = re.compile(
                rf"{re.escape(citation)}(?!\s*[（(]来源[：:])"
            )
            content = pattern.sub(f"{citation}（来源：{_source_label(item)}）", content)
    for item in cases:
        case_no = str(item.get("case_no") or "").strip()
        if not case_no:
            continue
        pattern = re.compile(
            rf"{re.escape(case_no)}(?!\s*[（(]来源[：:])"
        )
        content = pattern.sub(f"{case_no}（来源：{_source_label(item)}）", content)
    return content


def _soften_absolute_claims(content: str) -> str:
    for absolute, qualified in _ABSOLUTE_CLAIMS.items():
        content = content.replace(absolute, qualified)
    return content


def _citations_from_reports(state: AgentState) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    laws = _trusted_laws(state)
    cases = _trusted_cases(state)
    for report in state.get("agent_reports", []) or []:
        for source in report.get("sources", []) or []:
            if not isinstance(source, dict):
                continue
            if _looks_like_law(source) and not _matches_law(source, laws):
                continue
            if _looks_like_case(source) and not _matches_case(source, cases):
                continue
            if not (_looks_like_law(source) or _looks_like_case(source)):
                continue
            source_type = str(source.get("source_type") or "")
            source_id = _source_id(source)
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


async def _generate_answer(state: AgentState) -> str:
    explicit_verification = state.get("verification_result")
    verification = explicit_verification or verify_plan_results(state)
    laws = _trusted_laws(state)
    cases = _trusted_cases(state)
    payload = {
        "原始问题": _original_question(state),
        "专家报告": state.get("agent_reports", []) or [],
        "检索法条": laws,
        "检索案例": cases,
        "核验结果": verification,
    }
    fallback = _fallback_final_response(state, verification)
    try:
        llm_factory = compatibility_dependency("get_llm", get_llm)
        llm = llm_factory(
            provider=os.getenv("ANSWER_GENERATOR_PROVIDER", os.getenv("VERIFIER_PROVIDER", "deepseek")),
            model=os.getenv("ANSWER_GENERATOR_MODEL", os.getenv("VERIFIER_MODEL", "deepseek-v4-flash-vision-exp")),
            model_route="answer_generator",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0.2,
            streaming=False,
        )
        response = await llm.ainvoke([
            SystemMessage(content=ANSWER_GENERATOR_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
        ])
        content = strip_answer_markdown(str(response.content or "").strip()) or fallback
    except Exception as exc:
        record_trace_event(
            state.get("trace_id"),
            "agent_fallback",
            name="answer_generator",
            payload={"error": str(exc)},
        )
        content = fallback

    content = strip_answer_markdown(_guard_law_citations(content, laws))
    content = strip_answer_markdown(_guard_case_citations(content, cases))
    content = _add_inline_sources(content, laws, cases)
    content = _soften_absolute_claims(content)
    if explicit_verification and _has_uncertainty(verification) and not content.startswith("风险提示："):
        content = f"{_risk_notice(verification)}\n\n{content}"
    return content


async def answer_generator_node(state: AgentState) -> dict[str, Any]:
    """Return the final, report-grounded answer as a partial state update."""
    content = await _generate_answer(state)
    record_trace_event(
        state.get("trace_id"),
        "final_answer",
        name="answer_generator",
        payload={
            "content_preview": content[:500],
            "answer_score": score_legal_answer(
                _original_question(state),
                content,
                _trusted_laws(state),
            ),
        },
    )
    return {
        "citations": _citations_from_reports(state),
        "supervisor_route": "end",
        "supervisor_reason": "核验后答复已生成",
        "supervisor_finalized": True,
        "messages": [AIMessage(content=content)],
    }


# Compatibility for the old Supervisor helper import.
_llm_verifier_final_response = _generate_answer
