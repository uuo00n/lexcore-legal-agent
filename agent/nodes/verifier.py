"""Deterministic-first verification of plan results and citations."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from agent.agents.legal_consult_agent import _law_key
from agent.node_utils import compatibility_dependency, latest_human_message, record_trace_event
from agent.prompts import RESULT_VERIFIER_PROMPT
from agent.reports import report_agent_name
from agent.state import AgentState, PlanStep, VerificationResult
from agent.replan import MAX_AGENT_REPLAN_RETRIES, replan_retry_count
from services.llm import get_llm


# 兼容旧导入名；该预算只控制 Agent Replan，不控制任何 HTTP/LLM retry。
MAX_VERIFIER_RETRIES = MAX_AGENT_REPLAN_RETRIES
_LAW_CITATION_RE = re.compile(
    r"《([^》]+)》\s*"
    r"(第[一二三四五六七八九十百千万亿零〇两\d]+条(?:之[一二三四五六七八九十百千万亿零〇两\d]+)?)"
)
_CASE_NO_RE = re.compile(r"[（(][12]\d{3}[）)][^，。；;\s]{1,40}?号")
_OBSOLETE_MARKERS = (
    "废止",
    "失效",
    "已废止",
    "已失效",
    "expired",
    "repealed",
    "invalid",
    "abolished",
)
_KEY_CONCLUSION_MARKERS = (
    "应当",
    "有权",
    "无权",
    "违法",
    "有效",
    "无效",
    "承担责任",
    "违约责任",
    "构成",
    "不构成",
    "可以起诉",
    "可以仲裁",
    "胜诉",
    "赔偿",
)


class LLMVerificationSupplement(BaseModel):
    """Only the non-deterministic dimensions the verifier model may assess."""

    model_config = ConfigDict(extra="forbid")

    severe_conflicts: list[str] = Field(default_factory=list, max_length=10)
    unsupported_conclusions: list[str] = Field(default_factory=list, max_length=10)
    obsolete_law_risks: list[str] = Field(default_factory=list, max_length=10)
    missing_sources: list[str] = Field(default_factory=list, max_length=10)


@dataclass
class _VerificationAudit:
    issues: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    invalid_citations: list[str] = field(default_factory=list)
    failed_dimensions: set[str] = field(default_factory=set)

    def add_issue(self, dimension: str, issue: str) -> None:
        self.failed_dimensions.add(dimension)
        if issue not in self.issues:
            self.issues.append(issue)

    def add_missing_source(self, issue: str) -> None:
        self.add_issue("key_sources", issue)
        if issue not in self.missing_sources:
            self.missing_sources.append(issue)

    def add_invalid_citation(self, dimension: str, issue: str) -> None:
        self.add_issue(dimension, issue)
        if issue not in self.invalid_citations:
            self.invalid_citations.append(issue)


def _last_report_from(state: AgentState, agent_name: str) -> dict[str, Any] | None:
    for report in reversed(state.get("agent_reports", []) or []):
        if report_agent_name(report) == agent_name:
            return report
    return None


def _normalize_text(value: Any) -> str:
    return re.sub(r"[\s（）()]", "", str(value or "")).lower()


def _source_id(item: dict[str, Any]) -> str:
    return str(item.get("source_id") or item.get("case_id") or item.get("id") or "")


def _looks_like_case(item: dict[str, Any]) -> bool:
    source_type = str(item.get("source_type") or "").lower()
    return "case" in source_type or any(item.get(key) for key in ("case_id", "case_no", "case_name"))


def _looks_like_law(item: dict[str, Any]) -> bool:
    if _looks_like_case(item):
        return False
    source_type = str(item.get("source_type") or "").lower()
    return "law" in source_type or "rag" in source_type or any(
        item.get(key) for key in ("law_name", "article_no", "timeliness_name")
    )


def _matches_law(source: dict[str, Any], retrieved_laws: list[dict[str, Any]]) -> bool:
    source_identifier = _source_id(source)
    law_name = str(source.get("law_name") or source.get("title") or "")
    article_no = str(source.get("article_no") or "")
    if source_identifier:
        retrieved_identifiers = {_source_id(item) for item in retrieved_laws if _source_id(item)}
        identifier_matches = [item for item in retrieved_laws if _source_id(item) == source_identifier]
        if retrieved_identifiers and not identifier_matches:
            return False
        if identifier_matches and any(
            (
                not law_name
                or _law_key(law_name, "")[0]
                == _law_key(str(item.get("law_name") or item.get("title") or ""), "")[0]
            )
            and (
                not article_no
                or _normalize_text(item.get("article_no")) == _normalize_text(article_no)
            )
            for item in identifier_matches
        ):
            return True
    if not law_name:
        return False
    for item in retrieved_laws:
        item_name = str(item.get("law_name") or item.get("title") or "")
        item_article = str(item.get("article_no") or "")
        if _law_key(law_name, article_no) == _law_key(item_name, item_article):
            return True
        if not article_no and _normalize_text(law_name) == _normalize_text(item_name):
            return True
    return False


def _matches_case(source: dict[str, Any], retrieved_cases: list[dict[str, Any]]) -> bool:
    source_identifier = _source_id(source)
    if source_identifier:
        retrieved_identifiers = {_source_id(item) for item in retrieved_cases if _source_id(item)}
        identifier_matches = [item for item in retrieved_cases if _source_id(item) == source_identifier]
        if retrieved_identifiers:
            if not identifier_matches:
                return False
            for item in identifier_matches:
                case_no = _normalize_text(source.get("case_no"))
                case_name = _normalize_text(source.get("case_name") or source.get("title"))
                if case_no and case_no != _normalize_text(item.get("case_no")):
                    continue
                if case_name and case_name != _normalize_text(item.get("case_name") or item.get("title")):
                    continue
                return True
            return False
    for key in ("case_no", "case_name", "title"):
        value = _normalize_text(source.get(key))
        if value and any(
            value == _normalize_text(item.get(key) or item.get("case_name") or item.get("title"))
            for item in retrieved_cases
        ):
            return True
    return False


def _report_text(report: dict[str, Any]) -> str:
    content = {key: value for key, value in report.items() if key != "sources"}
    return json.dumps(content, ensure_ascii=False, default=str)


def _report_for_step(state: AgentState, step: PlanStep) -> dict[str, Any] | None:
    step_id = str(step.get("step_id") or "")
    assigned_agent = str(step.get("assigned_agent") or "")
    for report in reversed(state.get("agent_reports", []) or []):
        task_id = str(report.get("task_id") or report.get("step_id") or "")
        if task_id == step_id and (not assigned_agent or report_agent_name(report) == assigned_agent):
            return report
    result = step.get("result")
    return result if isinstance(result, dict) else None


def _has_key_conclusion(report: dict[str, Any]) -> bool:
    text = _report_text(report)
    return any(marker in text for marker in _KEY_CONCLUSION_MARKERS)


def _law_label(item: dict[str, Any]) -> str:
    name = str(item.get("law_name") or item.get("title") or "未知法规")
    article = str(item.get("article_no") or "")
    return f"《{name}》{article}"


def _case_label(item: dict[str, Any]) -> str:
    return str(
        item.get("case_no")
        or item.get("case_name")
        or item.get("title")
        or _source_id(item)
        or "未知案例"
    )


def _is_obsolete(item: dict[str, Any]) -> bool:
    status = " ".join(
        str(item.get(key) or "")
        for key in ("timeliness_name", "validity_status", "effectiveness", "status")
    ).lower()
    if "尚未失效" in status:
        return False
    return any(marker in status for marker in _OBSOLETE_MARKERS)


def _task_type_value(step: PlanStep) -> str:
    value = step.get("task_type")
    return str(getattr(value, "value", value) or "")


def _deterministic_audit(state: AgentState) -> _VerificationAudit:
    audit = _VerificationAudit()
    plan = list(state.get("plan", []) or [])
    reports = list(state.get("agent_reports", []) or [])
    retrieved_laws = list(state.get("retrieved_laws", []) or [])
    retrieved_cases = list(state.get("retrieved_cases", []) or [])

    if not plan:
        audit.add_issue("plan_completion", "Planner 未提供可核验的必须任务")
    for step in plan:
        if step.get("required", True) is False and step.get("status") == "skipped":
            continue
        step_id = str(step.get("step_id") or "unknown_step")
        status = str(step.get("status") or "pending")
        if status != "completed":
            audit.add_issue("plan_completion", f"{step_id} 必须任务未完成（{status}）")
            continue
        if not step.get("result"):
            audit.add_issue("plan_completion", f"{step_id} 缺少执行结果")
        if _report_for_step(state, step) is None:
            audit.add_issue("plan_completion", f"{step_id} 缺少对应的专家报告")

    used_laws: list[dict[str, Any]] = []
    for report in reports:
        report_name = report_agent_name(report) or str(report.get("report_id") or "未知报告")
        for source in report.get("sources", []) or []:
            if not isinstance(source, dict):
                continue
            if _looks_like_case(source):
                if not _matches_case(source, retrieved_cases):
                    audit.add_invalid_citation(
                        "case_sources",
                        f"{report_name} 引用了 retrieved_cases 中不存在的案例：{_case_label(source)}",
                    )
            elif _looks_like_law(source):
                if not _matches_law(source, retrieved_laws):
                    audit.add_invalid_citation(
                        "law_sources",
                        f"{report_name} 引用了 retrieved_laws 中不存在的法规：{_law_label(source)}",
                    )
                else:
                    used_laws.append(source)

        text = _report_text(report)
        for law_name, article_no in _LAW_CITATION_RE.findall(text):
            citation = {"law_name": law_name, "article_no": article_no}
            if not _matches_law(citation, retrieved_laws):
                audit.add_invalid_citation(
                    "fabricated_laws",
                    f"{report_name} 生成了检索结果中不存在的法条：《{law_name}》{article_no}",
                )
            else:
                used_laws.append(citation)
        allowed_case_numbers = {
            _normalize_text(item.get("case_no"))
            for item in retrieved_cases
            if item.get("case_no")
        }
        for case_no in _CASE_NO_RE.findall(text):
            if _normalize_text(case_no) not in allowed_case_numbers:
                audit.add_invalid_citation(
                    "case_sources",
                    f"{report_name} 引用了 retrieved_cases 中不存在的案号：{case_no}",
                )

    for step in plan:
        if step.get("status") != "completed":
            continue
        report = _report_for_step(state, step)
        if not report:
            continue
        sources = [item for item in report.get("sources", []) or [] if isinstance(item, dict)]
        valid_laws = [item for item in sources if _looks_like_law(item) and _matches_law(item, retrieved_laws)]
        valid_cases = [item for item in sources if _looks_like_case(item) and _matches_case(item, retrieved_cases)]
        findings = report.get("findings")
        findings_insufficient = findings.get("evidence_insufficient") if isinstance(findings, dict) else False
        evidence_insufficient = bool(report.get("evidence_insufficient") or findings_insufficient)
        task_type = _task_type_value(step)
        step_id = str(step.get("step_id") or "unknown_step")
        if task_type == "statute_retrieval" and not valid_laws and not evidence_insufficient:
            audit.add_missing_source(f"{step_id} 声称完成法规检索，但没有可信法规 source")
        if task_type == "case_retrieval" and not valid_cases and not evidence_insufficient:
            audit.add_missing_source(f"{step_id} 声称完成类案检索，但没有可信案例 source")
        if task_type == "legal_consultation" and _has_key_conclusion(report) and not (valid_laws or valid_cases):
            audit.add_issue("unsupported_conclusions", f"{step_id} 存在明显缺少依据的关键法律结论")
            audit.add_missing_source(f"{step_id} 的关键法律结论没有可信 source")

    fact_report = next(
        (
            report
            for report in reversed(reports)
            if report_agent_name(report) == "case_analysis_agent"
            and report.get("status") == "needs_more_facts"
        ),
        None,
    )
    consult_report = _last_report_from(state, "legal_consult_agent")
    if (
        fact_report
        and fact_report.get("status") == "needs_more_facts"
        and consult_report
        and _has_key_conclusion(consult_report)
    ):
        audit.add_issue("agent_conflicts", "案件报告认定关键事实不足，但法律咨询报告给出了确定性结论")

    for used in used_laws:
        for retrieved in retrieved_laws:
            if _matches_law(used, [retrieved]) and _is_obsolete(retrieved):
                audit.add_issue("obsolete_laws", f"引用法规存在失效风险：{_law_label(retrieved)}")
                break
    return audit


def _verification_result(audit: _VerificationAudit, *, replan_count: int) -> VerificationResult:
    dimensions = 8
    score = round((dimensions - len(audit.failed_dimensions)) / dimensions, 4)
    passed = not audit.issues
    needs_retry = not passed and replan_count < MAX_AGENT_REPLAN_RETRIES
    retry_reason = "；".join(audit.issues[:3]) if needs_retry else None
    return {
        "passed": passed,
        "score": max(0.0, score),
        "issues": list(audit.issues),
        "missing_sources": list(audit.missing_sources),
        "invalid_citations": list(audit.invalid_citations),
        "needs_retry": needs_retry,
        "retry_reason": retry_reason,
    }


def verify_plan_results(state: AgentState) -> VerificationResult:
    """Run the deterministic verification dimensions without invoking a model."""
    audit = _deterministic_audit(state)
    return _verification_result(
        audit,
        replan_count=replan_retry_count(state),
    )


def _ground_supplement_notes(notes: Iterable[str], source_text: str) -> list[str]:
    """Discard model notes that introduce citations absent from verifier input."""
    grounded: list[str] = []
    for raw_note in notes:
        note = str(raw_note).strip()[:300]
        if not note:
            continue
        law_citations = [f"《{name}》{article}" for name, article in _LAW_CITATION_RE.findall(note)]
        case_numbers = _CASE_NO_RE.findall(note)
        if any(citation not in source_text for citation in law_citations):
            continue
        if any(case_no not in source_text for case_no in case_numbers):
            continue
        if note not in grounded:
            grounded.append(note)
    return grounded


async def _llm_verification_supplement(state: AgentState, deterministic: VerificationResult) -> LLMVerificationSupplement:
    reports = list(state.get("agent_reports", []) or [])
    if not reports:
        return LLMVerificationSupplement()
    payload = {
        "用户问题": latest_human_message(state),
        "执行计划": state.get("plan", []) or [],
        "专家报告": reports,
        "检索法条": state.get("retrieved_laws", []) or [],
        "检索案例": state.get("retrieved_cases", []) or [],
        "确定性初检": deterministic,
    }
    try:
        llm_factory = compatibility_dependency("get_llm", get_llm)
        llm = llm_factory(
            provider=os.getenv("VERIFIER_PROVIDER", os.getenv("SUPERVISOR_PROVIDER", "deepseek")),
            model=os.getenv("VERIFIER_MODEL", os.getenv("SUPERVISOR_MODEL", "deepseek-v4-flash-vision-exp")),
            model_route="verifier",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0,
            streaming=False,
        )
        structured_llm = llm.with_structured_output(LLMVerificationSupplement)
        raw = await structured_llm.ainvoke([
            SystemMessage(content=RESULT_VERIFIER_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        return raw if isinstance(raw, LLMVerificationSupplement) else LLMVerificationSupplement.model_validate(raw)
    except Exception as exc:
        record_trace_event(
            state.get("trace_id"),
            "verifier_llm_skipped",
            name="verifier",
            payload={"error": str(exc)},
        )
        return LLMVerificationSupplement()


def _merge_llm_supplement(
    audit: _VerificationAudit,
    supplement: LLMVerificationSupplement,
    *,
    source_text: str,
) -> None:
    for note in _ground_supplement_notes(supplement.severe_conflicts, source_text):
        audit.add_issue("agent_conflicts", f"报告存在严重冲突：{note}")
    for note in _ground_supplement_notes(supplement.unsupported_conclusions, source_text):
        audit.add_issue("unsupported_conclusions", f"结论明显缺少依据：{note}")
    for note in _ground_supplement_notes(supplement.obsolete_law_risks, source_text):
        audit.add_issue("obsolete_laws", f"法规有效性存在风险：{note}")
    for note in _ground_supplement_notes(supplement.missing_sources, source_text):
        audit.add_missing_source(f"关键结论缺少 source：{note}")


def _retry_updates(state: AgentState) -> dict[str, Any]:
    reset_plan: list[PlanStep] = []
    for step in state.get("plan", []) or []:
        reset = dict(step)
        if not (reset.get("required", True) is False and reset.get("status") == "skipped"):
            reset["status"] = "pending"
            reset["result"] = None
        reset_plan.append(reset)  # type: ignore[arg-type]
    return {
        "plan": reset_plan,
        "current_step": None,
        "completed_steps": [],
        "remaining_steps": [dict(step) for step in reset_plan if step.get("status") == "pending"],
        "agent_reports": [],
        "retrieved_laws": [],
        "retrieved_cases": [],
        "citations": [],
        "retry_count": 0,
        "tool_call_count": 0,
        "tool_loop_failure": None,
        "supervisor_route": "replan",
        "supervisor_finalized": False,
    }


async def result_verifier_node(state: AgentState) -> dict[str, Any]:
    """Verify result state; never generate an answer or new legal facts."""
    audit = _deterministic_audit(state)
    initial = _verification_result(
        audit,
        replan_count=replan_retry_count(state),
    )
    supplement = await _llm_verification_supplement(state, initial)
    source_text = json.dumps(
        {
            "plan": state.get("plan", []) or [],
            "reports": state.get("agent_reports", []) or [],
            "laws": state.get("retrieved_laws", []) or [],
            "cases": state.get("retrieved_cases", []) or [],
        },
        ensure_ascii=False,
        default=str,
    )
    _merge_llm_supplement(audit, supplement, source_text=source_text)
    retry_count = replan_retry_count(state)
    verification = _verification_result(audit, replan_count=retry_count)
    record_trace_event(
        state.get("trace_id"),
        "verification_complete",
        name="verifier",
        payload=verification,
    )
    result: dict[str, Any] = {
        "verification_result": verification,
        "supervisor_route": "replan" if verification["needs_retry"] else "answer_generator",
        "supervisor_reason": verification["retry_reason"] or "结果核验完成",
        "supervisor_finalized": False,
    }
    if verification["needs_retry"]:
        result.update(_retry_updates(state))
        result["verification_result"] = verification
        result["supervisor_route"] = "replan"
        result["replan_retry_count"] = retry_count + 1
        # 仅用于读取旧 checkpoint/旧监控字段，新逻辑不得用它控制传输重试。
        result["verifier_retry_count"] = retry_count + 1
    return result


# Backward-compatible node name; the graph uses the explicit result name.
verifier_node = result_verifier_node
