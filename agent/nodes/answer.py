"""Generate a grounded final answer from verified graph state."""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.node_utils import compatibility_dependency, latest_human_message, record_trace_event
from agent.prompts import ANSWER_GENERATOR_PROMPT
from agent.state import AgentState, VerificationResult, VerifiedEvidence
from services.answer_format import strip_answer_markdown
from services.final_quality import measure_final_answer
from services.llm import get_llm
from services.model_defaults import STRONG, resolve_model, resolve_provider
from services.workflow_metrics import record_answer

from .answer_guard import (
    allowed_citation_labels,
    audit_answer_citations,
    keep_grounded_sentences,
    user_safe_risks,
)
from .citation_verifier import verify_citations
from .verifier import verify_plan_results


# §P2：草稿里出现未核验引用时最多重新生成一次，再失败就用确定性重建，
# 不再走「先生成坏引用、再字符串删除」的老路（§二 问题 10）。
MAX_ANSWER_ATTEMPTS = 2

_ABSOLUTE_CLAIMS = {
    "一定胜诉": "存在胜诉可能，但仍取决于证据和裁判认定",
    "必然胜诉": "存在胜诉可能，但仍取决于证据和裁判认定",
    "肯定能赢": "存在胜诉可能，但仍取决于证据和裁判认定",
    "百分百胜诉": "存在胜诉可能，但仍取决于证据和裁判认定",
    "百分之百胜诉": "存在胜诉可能，但仍取决于证据和裁判认定",
}


def _original_question(state: AgentState) -> str:
    return str(state.get("original_query") or latest_human_message(state) or "")


def verified_evidence_of(state: AgentState) -> VerifiedEvidence:
    """P0-1：答案与引用只能来自唯一核验真相源 ``verified_evidence``。

    Verifier 已写入时直接复用；旧 checkpoint 或跳过 Verifier 的链路
    则用同一套确定性规则现算，保证两条路径口径一致。
    """
    evidence = state.get("verified_evidence")
    if isinstance(evidence, dict) and any(
        evidence.get(key) for key in ("laws", "cases", "citations", "checks")
    ):
        return evidence  # type: ignore[return-value]
    computed, _issues = verify_citations(state)
    return computed


def _trusted_laws(state: AgentState, evidence: VerifiedEvidence | None = None) -> list[dict[str, Any]]:
    source = evidence if evidence is not None else verified_evidence_of(state)
    return [dict(item) for item in source.get("laws", []) or []]


def _trusted_cases(state: AgentState, evidence: VerifiedEvidence | None = None) -> list[dict[str, Any]]:
    source = evidence if evidence is not None else verified_evidence_of(state)
    return [dict(item) for item in source.get("cases", []) or []]



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
    """风险小节文本；只用面向用户的表述，不回显核验问题原文（§P2）。"""
    statements = user_safe_risks(verification)
    return "；".join(statements[:5]) or "现有材料尚未通过完整核验，结论仅供参考"


def _has_uncertainty(verification: VerificationResult) -> bool:
    return not verification.get("passed", False) or any(
        verification.get(key) for key in ("issues", "missing_sources", "invalid_citations")
    )


def _risk_notice(verification: VerificationResult) -> str:
    return (
        f"风险提示：{_risk_detail(verification)}。"
        "以下内容仅基于当前可核验材料，不能视为确定或完整的法律结论。"
    )


def _missing_facts(state: AgentState) -> list[str]:
    """本轮仍需用户补充的事实（§七 非阻断的「先答再问」）。"""
    values: list[str] = []
    for item in state.get("missing_facts", []) or []:
        text = str(item).strip()
        if text and text not in values:
            values.append(text)
    return values[:6]


def _fallback_final_response(
    state: AgentState,
    verification: VerificationResult,
    laws: list[dict[str, Any]] | None = None,
    cases: list[dict[str, Any]] | None = None,
) -> str:
    """确定性重建最终答复：只用已核验证据与能通过引用核验的报告正文（§P2）。"""
    trusted_laws = _trusted_laws(state) if laws is None else laws
    trusted_cases = _trusted_cases(state) if cases is None else cases
    reports = list(state.get("agent_reports", []) or [])
    latest = reports[-1] if reports else {}
    # 报告正文可能带着没通过核验的引用，这里按句丢弃而不是按字符替换：
    # 用户拿到的是完整句子，不是「已移除」的替换标记（§二 问题 10）。
    conclusion = keep_grounded_sentences(
        _report_text(latest, "draft_response", "final_response", "summary"),
        trusted_laws,
        trusted_cases,
    )
    analysis = keep_grounded_sentences(
        _report_text(latest, "analysis", "summary"),
        trusted_laws,
        trusted_cases,
    )
    questions = _report_list(latest, "suggested_questions", "questions")
    next_steps = _report_list(latest, "next_steps", "suggested_actions", "recommendations")

    if not conclusion:
        conclusion = "现有专家报告不足以形成可核验的明确结论。"
    if not analysis:
        analysis = "目前只能基于已提交的专家报告和检索材料作有限分析。"

    law_lines = _law_reference_lines(trusted_laws)
    case_lines = _case_reference_lines(trusted_cases)
    risk = _risk_detail(verification) if _has_uncertainty(verification) else "仍需结合完整事实、证据和有效法律文本审慎判断。"
    if questions:
        risk = f"{risk}；尚需确认：{'；'.join(questions[:3])}"

    sections = [
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
    ]
    missing = _missing_facts(state)
    if missing:
        # 事实缺口不阻断作答时，也必须让用户看到还缺什么（§七）。
        sections.extend([
            "7. 需要补充的信息",
            "\n".join(f"- {item}" for item in missing),
        ])
    return "\n".join(sections)


# 报告里只有这些字段对最终答复有用；report_id / task_id / agent_name / raw_response
# 属于执行链内部信息，按 §P2 不进 prompt，也就不可能被模型抄进用户答复。
_REPORT_PAYLOAD_KEYS = ("summary", "findings", "sources", "confidence")


def _sanitized_reports(state: AgentState) -> list[dict[str, Any]]:
    """交给模型的专家报告视图：去掉内部标识与原始模型输出。"""
    reports: list[dict[str, Any]] = []
    for report in state.get("agent_reports", []) or []:
        if not isinstance(report, dict):
            continue
        view = {key: report[key] for key in _REPORT_PAYLOAD_KEYS if key in report}
        findings = view.get("findings")
        if isinstance(findings, dict):
            view["findings"] = {
                key: value for key, value in findings.items() if key != "raw_response"
            }
        if view:
            reports.append(view)
    return reports


def _sanitized_verification(verification: VerificationResult) -> dict[str, Any]:
    """交给模型的核验视图（§P2）。

    原实现把 ``verification_result`` 整个塞进 prompt，里面带着内部 Agent 名、step_id
    和被判定为编造的引用原文——模型很容易把这些照抄进答复，等于把核验内部信息
    直接暴露给用户。这里只保留结论、面向用户的风险表述和引用统计。
    """
    report = verification.get("citation_report")
    return {
        "是否通过": bool(verification.get("passed", False)),
        "风险提示": user_safe_risks(verification),
        "引用统计": dict(report) if isinstance(report, dict) else {},
    }


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


def _final_citations(evidence: VerifiedEvidence) -> list[dict[str, Any]]:
    """最终引用只从 ``verified_evidence.citations`` 派生（P0-1），不再重扫报告。"""
    citations: list[dict[str, Any]] = []
    for item in evidence.get("citations", []) or []:
        if not isinstance(item, dict):
            continue
        citation = dict(item)
        # 归一化前的旧 checkpoint 证据没有 evidence_id，这里补一个稳定序号。
        if not citation.get("citation_id"):
            citation["citation_id"] = f"citation_{len(citations) + 1}"
        if not citation.get("url"):
            citation["url"] = ""
        citations.append(citation)
    return citations


def _answer_payload(
    state: AgentState,
    verification: VerificationResult,
    laws: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    allowed_labels: list[str] | None = None,
) -> dict[str, Any]:
    """Answer Generator 的输入：只有已核验证据与脱敏后的核验结论。"""
    payload: dict[str, Any] = {
        "原始问题": _original_question(state),
        "专家报告": _sanitized_reports(state),
        "检索法条": laws,
        "检索案例": cases,
        "核验结果": _sanitized_verification(verification),
        # 非阻断的事实缺口在这里交给答案，而不是让用户空等一次补问（§七）。
        "待补充事实": _missing_facts(state),
    }
    if allowed_labels is not None:
        # 第二次生成才带允许清单：只从已核验状态重新生成，而不是修补上一稿（§P2）。
        payload["允许引用"] = allowed_labels
        payload["重写要求"] = (
            "上一稿写出了不在「允许引用」内的法条或案号。请只使用清单里的写法，"
            "不得出现清单之外的任何法条编号或案号；没有依据的判断改写成不带引用的表述。"
        )
    return payload


async def _invoke_answer_llm(state: AgentState, payload: dict[str, Any]) -> str:
    llm_factory = compatibility_dependency("get_llm", get_llm)
    llm = llm_factory(
        provider=resolve_provider(
            "ANSWER_GENERATOR_PROVIDER", "VERIFIER_PROVIDER", "SUPERVISOR_PROVIDER", tier=STRONG
        ),
        model=resolve_model(
            "ANSWER_GENERATOR_MODEL", "VERIFIER_MODEL", "SUPERVISOR_MODEL", tier=STRONG
        ),
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
    return strip_answer_markdown(str(response.content or "").strip())


async def _generate_answer(state: AgentState, evidence: VerifiedEvidence | None = None) -> str:
    """生成最终答复：草稿必须整体通过引用核验，否则重生成一次再退回确定性重建（§P2）。"""
    explicit_verification = state.get("verification_result")
    verification = explicit_verification or verify_plan_results(state)
    evidence = evidence if evidence is not None else verified_evidence_of(state)
    laws = _trusted_laws(state, evidence)
    cases = _trusted_cases(state, evidence)

    content = ""
    attempts = 0
    outcome = "deterministic"
    allowed_labels: list[str] | None = None
    for attempt in range(1, MAX_ANSWER_ATTEMPTS + 1):
        attempts = attempt
        payload = _answer_payload(state, verification, laws, cases, allowed_labels)
        try:
            draft = await _invoke_answer_llm(state, payload)
        except Exception as exc:
            record_trace_event(
                state.get("trace_id"),
                "agent_fallback",
                name="answer_generator",
                payload={"error": str(exc), "attempt": attempt},
            )
            break
        if not draft:
            break
        audit = audit_answer_citations(draft, laws, cases)
        if audit.grounded:
            content = draft
            outcome = "model" if attempt == 1 else "rewritten"
            break
        record_trace_event(
            state.get("trace_id"),
            "answer_citation_rejected",
            name="answer_generator",
            payload={"attempt": attempt, "ungrounded": list(audit.labels)[:10]},
        )
        allowed_labels = allowed_citation_labels(laws, cases)

    if not content:
        # 两次生成都带未核验引用（或 Provider 失败）时改用确定性重建：
        # 按句丢弃 + 已核验证据直接渲染，不对模型输出做字符串删除（§二 问题 10）。
        content = _fallback_final_response(state, verification, laws, cases)

    # §二十五：答复来源分布是「重写率 / 确定性兜底率」的口径，与重试次数一起上报。
    record_answer(outcome, attempts=max(1, attempts))
    content = _add_inline_sources(content, laws, cases)
    content = _soften_absolute_claims(content)
    if explicit_verification and _has_uncertainty(verification) and not content.startswith("风险提示："):
        content = f"{_risk_notice(verification)}\n\n{content}"
    return content


async def answer_generator_node(state: AgentState) -> dict[str, Any]:
    """Return the final, report-grounded answer as a partial state update."""
    evidence = verified_evidence_of(state)
    content = await _generate_answer(state, evidence)
    # §P2：answer_score 只在生成答复的节点算一次，API 层复用 State 里的结果，
    # 不再出现两套并行的最终评分（§二 问题 12）。
    metrics = measure_final_answer(
        _original_question(state),
        content,
        _trusted_laws(state, evidence),
    )
    record_trace_event(
        state.get("trace_id"),
        "final_answer",
        name="answer_generator",
        payload={
            "content_preview": content[:500],
            "answer_score": metrics.as_dict(),
        },
    )
    return {
        "citations": _final_citations(evidence),
        "answer_score": metrics.as_dict(),
        "supervisor_route": "end",
        "supervisor_reason": "核验后答复已生成",
        "supervisor_finalized": True,
        "messages": [AIMessage(content=content)],
    }


# Compatibility for the old Supervisor helper import.
_llm_verifier_final_response = _generate_answer
