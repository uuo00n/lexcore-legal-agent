"""Deterministic-first verification of plan results and citations."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from agent.node_utils import compatibility_dependency, latest_human_message, record_trace_event
from agent.nodes.citation_verifier import (
    CASE_NO_RE,
    LAW_CITATION_RE as _LAW_CITATION_RE,
    EvidenceIndex,
    build_evidence_index,
    evidence_validity,
    law_label as _law_label,
    looks_like_case as _looks_like_case,
    looks_like_law as _looks_like_law,
    match_case,
    match_law,
    partially_valid_laws,
    report_text as _report_text,
    verify_citations,
)
from agent.prompts import RESULT_VERIFIER_PROMPT
from agent.reports import report_agent_name
from agent.state import (
    AgentState,
    PlanStep,
    VerificationIssue,
    VerificationResult,
    VerifiedEvidence,
)
from agent.repair import MAX_REPAIR_ROUNDS, repair_round_count, repair_targets_for
from agent.replan import MAX_AGENT_REPLAN_RETRIES, replan_retry_count
from services.llm import get_llm
from services.model_defaults import STRONG, resolve_model, resolve_provider
from services.workflow_metrics import record_replan, record_verification


# 兼容旧导入名；该预算只控制 Agent Replan，不控制任何 HTTP/LLM retry。
MAX_VERIFIER_RETRIES = MAX_AGENT_REPLAN_RETRIES
_CASE_NO_RE = CASE_NO_RE
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

# 评分维度固定为 8 项；每项最多扣一次分，评分与 answer_score 口径保持稳定。
_VERIFICATION_DIMENSIONS = (
    "plan_completion",
    "citation_consistency",
    "law_sources",
    "case_sources",
    "key_sources",
    "unsupported_conclusions",
    "agent_conflicts",
    "obsolete_laws",
)

# 结构化问题类型 → 旧版字符串字段的归属，保证既有消费方语义不变。
_CITATION_ISSUE_TYPES = frozenset({"citation_invalid", "obsolete_law"})
_MISSING_SOURCE_ISSUE_TYPES = frozenset({"retrieval_insufficient", "case_evidence_insufficient"})

# 重命名过渡期同时接受新旧 Agent 名（§四 允许保留兼容别名）。
_FACT_AGENT_NAMES = ("fact_analysis_agent", "case_analysis_agent")
_REASONING_AGENT_NAMES = ("legal_reasoning_agent", "legal_consult_agent")

# §十四：语义核验只允许给出这些问题类型，且必须能被 §P0-5 的修复路由表消化。
# ``citation_invalid`` 不在其中——引用是否成立只由确定性核验判定（§一 禁止让模型
# 重新判断引用），语义半边给出的引用类结论一律丢弃。
_SEMANTIC_ISSUE_DIMENSIONS: dict[str, str] = {
    "reasoning_conflict": "agent_conflicts",
    "overconfident": "unsupported_conclusions",
    "obsolete_law_risk": "obsolete_laws",
    "retrieval_insufficient": "key_sources",
    "case_evidence_insufficient": "case_sources",
}
# 问题文本前缀：与旧版四个字符串列表的措辞保持一致，避免下游文案口径漂移。
_SEMANTIC_ISSUE_PREFIXES: dict[str, str] = {
    "reasoning_conflict": "报告存在严重冲突",
    "overconfident": "结论明显缺少依据",
    "obsolete_law_risk": "法规有效性存在风险",
    "retrieval_insufficient": "关键结论缺少 source",
    "case_evidence_insufficient": "类案证据不足",
}


class SemanticVerificationIssue(BaseModel):
    """语义核验给出的单个结构化问题（§十四：``{type, step_id, agent, message}``）。"""

    model_config = ConfigDict(extra="forbid")

    type: str = ""
    step_id: str = ""
    agent: str = ""
    message: str = ""


class LLMVerificationSupplement(BaseModel):
    """Only the non-deterministic dimensions the verifier model may assess."""

    model_config = ConfigDict(extra="forbid")

    issues: list[SemanticVerificationIssue] = Field(default_factory=list, max_length=10)
    severe_conflicts: list[str] = Field(default_factory=list, max_length=10)
    unsupported_conclusions: list[str] = Field(default_factory=list, max_length=10)
    obsolete_law_risks: list[str] = Field(default_factory=list, max_length=10)
    missing_sources: list[str] = Field(default_factory=list, max_length=10)


@dataclass(frozen=True)
class _SemanticVerification:
    """语义核验的结果与降级状态（§P1-6）。"""

    supplement: LLMVerificationSupplement = field(default_factory=LLMVerificationSupplement)
    degraded: bool = False
    error: str = ""



@dataclass
class _VerificationAudit:
    """累积结构化核验问题，并同步维护旧版字符串字段以兼容既有消费方。"""

    issues: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    invalid_citations: list[str] = field(default_factory=list)
    failed_dimensions: set[str] = field(default_factory=set)
    structured_issues: list[VerificationIssue] = field(default_factory=list)
    _seen: set[tuple[str, str]] = field(default_factory=set)

    def mark(self, dimension: str) -> None:
        """只降低评分维度，不产生新的问题文本。"""
        self.failed_dimensions.add(dimension)

    def record(self, issue: VerificationIssue, *, dimension: str) -> None:
        """记录结构化问题；warning 只用于风险提示，不阻断核验。"""
        issue_type = str(issue.get("type") or "unknown")
        message = str(issue.get("message") or "").strip()
        key = (issue_type, message)
        if key in self._seen:
            return
        self._seen.add(key)
        self.structured_issues.append(issue)
        if issue.get("severity") == "warning":
            return
        self.mark(dimension)
        if message and message not in self.issues:
            self.issues.append(message)
        if issue_type in _CITATION_ISSUE_TYPES and message not in self.invalid_citations:
            self.invalid_citations.append(message)
        elif issue_type in _MISSING_SOURCE_ISSUE_TYPES and message not in self.missing_sources:
            self.missing_sources.append(message)

    def add(
        self,
        issue_type: str,
        message: str,
        *,
        dimension: str,
        severity: str = "blocking",
        source: str = "deterministic",
        agent: str = "",
        step_id: str = "",
        evidence_id: str = "",
    ) -> None:
        issue: VerificationIssue = {
            "type": issue_type,
            "severity": severity,  # type: ignore[typeddict-item]
            "source": source,  # type: ignore[typeddict-item]
            "message": message,
        }
        if agent:
            issue["agent"] = agent
        if step_id:
            issue["step_id"] = step_id
        if evidence_id:
            issue["evidence_id"] = evidence_id
        self.record(issue, dimension=dimension)


def _last_report_from(state: AgentState, *agent_names: str) -> dict[str, Any] | None:
    wanted = {name for name in agent_names if name}
    for report in reversed(state.get("agent_reports", []) or []):
        if report_agent_name(report) in wanted:
            return report
    return None


def _report_for_step(state: AgentState, step: PlanStep) -> dict[str, Any] | None:
    step_id = str(step.get("step_id") or "")
    assigned_agent = str(step.get("assigned_agent") or "")
    for report in reversed(state.get("agent_reports", []) or []):
        task_id = str(report.get("task_id") or report.get("step_id") or "")
        if task_id == step_id and (not assigned_agent or report_agent_name(report) == assigned_agent):
            return report
    result = step.get("result")
    return result if isinstance(result, dict) else None


def _report_id(report: Mapping[str, Any]) -> str:
    """与 Citation Verifier 使用同一口径，保证 check 能回溯到报告。"""
    return str(report.get("task_id") or report.get("step_id") or report.get("report_id") or "")


def _has_key_conclusion(report: dict[str, Any]) -> bool:
    text = _report_text(report)
    return any(marker in text for marker in _KEY_CONCLUSION_MARKERS)


def _declared_evidence(index: EvidenceIndex, evidence_id: str) -> tuple[Mapping[str, Any] | None, str]:
    """按 ``evidence_id`` 或 ``ref_id`` 反查本轮证据（§二十 报告声明的 evidence_ids）。"""
    key = str(evidence_id or "").strip()
    if not key:
        return None, ""
    law = index.law_by_evidence_id.get(key)
    if law is not None:
        return law, "law"
    case = index.case_by_evidence_id.get(key)
    if case is not None:
        return case, "case"
    for item in index.laws:
        if str(item.get("ref_id") or "") == key:
            return item, "law"
    for item in index.cases:
        if str(item.get("ref_id") or "") == key:
            return item, "case"
    return None, ""


def _verified_source_counts(report: Mapping[str, Any], index: EvidenceIndex) -> tuple[int, int]:
    """统计报告显式声明的可信法条与案例数量。

    只看报告自己声明的 ``sources`` 与 ``evidence_ids``（§二十），不把正文里
    顺带出现的引用当作依据——正文扫描是引用真实性的判据，不是「结论有依据」的判据。
    """
    law_hits = 0
    case_hits = 0
    for source in report.get("sources", []) or []:
        if not isinstance(source, Mapping):
            continue
        if _looks_like_case(source):
            evidence, _ = match_case(source, index)
            if evidence is not None:
                case_hits += 1
        elif _looks_like_law(source):
            evidence, _ = match_law(source, index)
            if evidence is not None and evidence_validity(evidence) != "invalid":
                law_hits += 1
    for evidence_id in report.get("evidence_ids", []) or []:
        evidence, kind = _declared_evidence(index, str(evidence_id))
        if evidence is None:
            continue
        if kind == "case":
            case_hits += 1
        elif evidence_validity(evidence) != "invalid":
            law_hits += 1
    return law_hits, case_hits


def _task_type_value(step: PlanStep) -> str:
    value = step.get("task_type")
    return str(getattr(value, "value", value) or "")


def _deterministic_audit(state: AgentState) -> tuple[_VerificationAudit, VerifiedEvidence]:
    """只用 Python 规则核验计划完成度、引用可信度与报告一致性（§十四）。"""
    audit = _VerificationAudit()
    plan = list(state.get("plan", []) or [])
    reports = list(state.get("agent_reports", []) or [])
    index = build_evidence_index(state)
    verified_evidence, citation_issues = verify_citations(state)
    checks = list(verified_evidence.get("checks", []) or [])

    if not plan:
        audit.add(
            "plan_incomplete",
            "Planner 未提供可核验的必须任务",
            dimension="plan_completion",
        )
    for step in plan:
        if step.get("required", True) is False and step.get("status") == "skipped":
            continue
        step_id = str(step.get("step_id") or "unknown_step")
        status = str(step.get("status") or "pending")
        agent = str(step.get("assigned_agent") or "")
        if status != "completed":
            audit.add(
                "plan_incomplete",
                f"{step_id} 必须任务未完成（{status}）",
                dimension="plan_completion",
                step_id=step_id,
                agent=agent,
            )
            continue
        if not step.get("result"):
            audit.add(
                "plan_incomplete",
                f"{step_id} 缺少执行结果",
                dimension="plan_completion",
                step_id=step_id,
                agent=agent,
            )
        if _report_for_step(state, step) is None:
            audit.add(
                "plan_incomplete",
                f"{step_id} 缺少对应的专家报告",
                dimension="plan_completion",
                step_id=step_id,
                agent=agent,
            )

    # 引用核验完全由确定性 Citation Verifier 判定，模型不得推翻。
    for issue in citation_issues:
        audit.record(issue, dimension="citation_consistency")
    if any(not check.get("verified") and check.get("kind") == "law" for check in checks):
        audit.mark("law_sources")
    if any(not check.get("verified") and check.get("kind") == "case" for check in checks):
        audit.mark("case_sources")

    for step in plan:
        if step.get("status") != "completed":
            continue
        report = _report_for_step(state, step)
        if not report:
            continue
        law_hits, case_hits = _verified_source_counts(report, index)
        findings = report.get("findings")
        findings_insufficient = findings.get("evidence_insufficient") if isinstance(findings, dict) else False
        evidence_insufficient = bool(report.get("evidence_insufficient") or findings_insufficient)
        task_type = _task_type_value(step)
        step_id = str(step.get("step_id") or "unknown_step")
        agent = report_agent_name(report) or str(step.get("assigned_agent") or "")
        if task_type == "statute_retrieval" and not law_hits and not evidence_insufficient:
            audit.add(
                "retrieval_insufficient",
                f"{step_id} 声称完成法规检索，但没有可信法规 source",
                dimension="key_sources",
                step_id=step_id,
                agent=agent,
            )
        if task_type == "case_retrieval" and not case_hits and not evidence_insufficient:
            audit.add(
                "case_evidence_insufficient",
                f"{step_id} 声称完成类案检索，但没有可信案例 source",
                dimension="key_sources",
                step_id=step_id,
                agent=agent,
            )
        if task_type == "legal_consultation" and _has_key_conclusion(report) and not (law_hits or case_hits):
            audit.add(
                "overconfident",
                f"{step_id} 存在明显缺少依据的关键法律结论",
                dimension="unsupported_conclusions",
                step_id=step_id,
                agent=agent,
            )
            audit.add(
                "retrieval_insufficient",
                f"{step_id} 的关键法律结论没有可信 source",
                dimension="key_sources",
                step_id=step_id,
                agent=agent,
            )

    fact_report = next(
        (
            report
            for report in reversed(reports)
            if report_agent_name(report) in _FACT_AGENT_NAMES
            and report.get("status") == "needs_more_facts"
        ),
        None,
    )
    consult_report = _last_report_from(state, *_REASONING_AGENT_NAMES)
    if fact_report and consult_report and _has_key_conclusion(consult_report):
        audit.add(
            "reasoning_conflict",
            "案件报告认定关键事实不足，但法律咨询报告给出了确定性结论",
            dimension="agent_conflicts",
            step_id=_report_id(consult_report),
            agent=report_agent_name(consult_report),
        )

    for item in partially_valid_laws(state):
        audit.add(
            "obsolete_law",
            f"引用法规存在部分失效风险：{_law_label(item)}",
            dimension="obsolete_laws",
            severity="warning",
            evidence_id=str(item.get("evidence_id") or ""),
        )
    return audit, verified_evidence


def _citation_report(verified_evidence: VerifiedEvidence) -> dict[str, int]:
    return {
        "citation_total": int(verified_evidence.get("citation_total", 0) or 0),
        "citation_verified": int(verified_evidence.get("citation_verified", 0) or 0),
        "citation_unsupported": int(verified_evidence.get("citation_unsupported", 0) or 0),
    }


def _verification_result(
    audit: _VerificationAudit,
    verified_evidence: VerifiedEvidence,
    *,
    replan_count: int,
    repair_count: int = 0,
    degraded: bool = False,
) -> VerificationResult:
    dimensions = len(_VERIFICATION_DIMENSIONS)
    score = round((dimensions - len(audit.failed_dimensions)) / dimensions, 4)
    passed = not audit.issues
    repair_targets = repair_targets_for(audit.structured_issues)
    # §P0-5：只要问题能落到某个执行单元，就用局部修复预算；只有计划本身不可修复
    # （没有任何修复目标）时才动用整体重排预算，两者互不消耗。
    budget_left = (
        repair_count < MAX_REPAIR_ROUNDS
        if repair_targets
        else replan_count < MAX_AGENT_REPLAN_RETRIES
    )
    needs_retry = not passed and budget_left
    retry_reason = "；".join(audit.issues[:3]) if needs_retry else None
    return {
        "passed": passed,
        "score": max(0.0, score),
        "issues": list(audit.issues),
        "missing_sources": list(audit.missing_sources),
        "invalid_citations": list(audit.invalid_citations),
        "needs_retry": needs_retry,
        "retry_reason": retry_reason,
        "structured_issues": list(audit.structured_issues),
        "citation_report": _citation_report(verified_evidence),
        # §P1-6：语义半边失败只降级，不影响确定性核验的结论，也不抛 500。
        "verification_degraded": degraded,
        "repair_targets": repair_targets,
    }


def verify_plan_results(state: AgentState) -> VerificationResult:
    """Run the deterministic verification dimensions without invoking a model."""
    audit, verified_evidence = _deterministic_audit(state)
    return _verification_result(
        audit,
        verified_evidence,
        replan_count=replan_retry_count(state),
        repair_count=repair_round_count(state),
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


async def _llm_verification_supplement(
    state: AgentState,
    deterministic: VerificationResult,
) -> _SemanticVerification:
    """Semantic Verifier：只补充难以规则化的判断，失败即降级（§十四、§P1-6）。"""
    reports = list(state.get("agent_reports", []) or [])
    if not reports:
        return _SemanticVerification()
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
            provider=resolve_provider("VERIFIER_PROVIDER", "SUPERVISOR_PROVIDER", tier=STRONG),
            model=resolve_model("VERIFIER_MODEL", "SUPERVISOR_MODEL", tier=STRONG),
            model_route="verifier",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0,
            streaming=False,
        )
        structured_llm = llm.with_structured_output(LLMVerificationSupplement)
        raw = await structured_llm.ainvoke([
            SystemMessage(content=RESULT_VERIFIER_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
        ])
        supplement = (
            raw if isinstance(raw, LLMVerificationSupplement)
            else LLMVerificationSupplement.model_validate(raw)
        )
        return _SemanticVerification(supplement=supplement)
    except Exception as exc:
        # §P1-6：Provider 报错、结构化输出不合法都只降级；确定性核验已经跑完，
        # 这里既不重试也不向上抛异常，避免整条链路 500。
        record_trace_event(
            state.get("trace_id"),
            "verifier_llm_skipped",
            name="verifier",
            payload={"error": str(exc)},
        )
        record_trace_event(
            state.get("trace_id"),
            "verification_degraded",
            name="verifier",
            payload={"reason": "semantic_verifier_unavailable", "error": str(exc)[:300]},
        )
        return _SemanticVerification(degraded=True, error=str(exc)[:300])


def _known_step_ids(state: AgentState) -> set[str]:
    return {
        str(step.get("step_id") or "")
        for step in state.get("plan", []) or []
        if step.get("step_id")
    }


def _known_agent_names(state: AgentState) -> set[str]:
    """计划与报告里真实出现过的 Agent 名；模型编出来的名字不予采纳。"""
    names = {
        str(step.get("assigned_agent") or "")
        for step in state.get("plan", []) or []
        if step.get("assigned_agent")
    }
    for report in state.get("agent_reports", []) or []:
        name = report_agent_name(report)
        if name:
            names.add(name)
    return names


def _merge_semantic_issues(
    audit: _VerificationAudit,
    issues: Iterable[SemanticVerificationIssue],
    *,
    source_text: str,
    step_ids: set[str],
    agent_names: set[str],
) -> None:
    """并入语义半边的结构化问题；类型、引用与归属都要经过确定性校验。"""
    for issue in issues:
        issue_type = str(issue.type or "").strip()
        dimension = _SEMANTIC_ISSUE_DIMENSIONS.get(issue_type)
        if dimension is None:
            continue
        grounded = _ground_supplement_notes([issue.message], source_text)
        if not grounded:
            continue
        step_id = str(issue.step_id or "").strip()
        agent = str(issue.agent or "").strip()
        audit.add(
            issue_type,
            f"{_SEMANTIC_ISSUE_PREFIXES[issue_type]}：{grounded[0]}",
            dimension=dimension,
            source="semantic",
            agent=agent if agent in agent_names else "",
            step_id=step_id if step_id in step_ids else "",
        )


def _merge_llm_supplement(
    audit: _VerificationAudit,
    supplement: LLMVerificationSupplement,
    *,
    source_text: str,
    state: AgentState | None = None,
) -> None:
    """把语义核验结论并入审计；引用是否成立仍只由确定性核验决定。"""
    _merge_semantic_issues(
        audit,
        supplement.issues,
        source_text=source_text,
        step_ids=_known_step_ids(state or {}),
        agent_names=_known_agent_names(state or {}),
    )
    # 旧版四个字符串列表继续支持：模型未升级到 issues 字段时行为不变。
    for note in _ground_supplement_notes(supplement.severe_conflicts, source_text):
        audit.add(
            "reasoning_conflict",
            f"报告存在严重冲突：{note}",
            dimension="agent_conflicts",
            source="semantic",
        )
    for note in _ground_supplement_notes(supplement.unsupported_conclusions, source_text):
        audit.add(
            "overconfident",
            f"结论明显缺少依据：{note}",
            dimension="unsupported_conclusions",
            source="semantic",
        )
    for note in _ground_supplement_notes(supplement.obsolete_law_risks, source_text):
        audit.add(
            "obsolete_law_risk",
            f"法规有效性存在风险：{note}",
            dimension="obsolete_laws",
            source="semantic",
        )
    for note in _ground_supplement_notes(supplement.missing_sources, source_text):
        audit.add(
            "retrieval_insufficient",
            f"关键结论缺少 source：{note}",
            dimension="key_sources",
            source="semantic",
        )


def _retry_updates(state: AgentState) -> dict[str, Any]:
    """重排计划以便重新执行；已归一化的证据必须保留（P0-6）。

    ToolMessage 只会被 Evidence Normalizer 消费一次，清空 ``retrieved_laws`` /
    ``retrieved_cases`` 会让第一轮证据无法恢复，因此这里只重置执行痕迹。
    ``tool_query_signatures`` 同样保留：整体重排不等于要把同样的检索再跑一遍
    （§二 问题 4），需要刷新检索的场景由 Repair Router 负责（§二十二）。
    """
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
        "citations": [],
        "retry_count": 0,
        "tool_call_count": 0,
        "tool_loop_failure": None,
        "tool_refresh_allowed": False,
        "supervisor_route": "replan",
        "supervisor_finalized": False,
    }


async def result_verifier_node(state: AgentState) -> dict[str, Any]:
    """Verify result state; never generate an answer or new legal facts."""
    audit, verified_evidence = _deterministic_audit(state)
    repair_count = repair_round_count(state)
    initial = _verification_result(
        audit,
        verified_evidence,
        replan_count=replan_retry_count(state),
        repair_count=repair_count,
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
    _merge_llm_supplement(audit, supplement.supplement, source_text=source_text, state=state)
    retry_count = replan_retry_count(state)
    verification = _verification_result(
        audit,
        verified_evidence,
        replan_count=retry_count,
        repair_count=repair_count,
        degraded=supplement.degraded,
    )
    record_trace_event(
        state.get("trace_id"),
        "verification_complete",
        name="verifier",
        payload=verification,
    )
    # §二十五：引用可追溯比例（§二十六「100% 引用可追溯」）的分子分母只从这里出。
    citation_report = verification.get("citation_report") or {}
    record_verification(
        passed=bool(verification["passed"]),
        degraded=bool(verification.get("verification_degraded")),
        citation_verified=int(citation_report.get("citation_verified", 0) or 0),
        citation_unsupported=int(citation_report.get("citation_unsupported", 0) or 0),
    )
    for issue in verification.get("structured_issues", []) or []:
        record_trace_event(
            state.get("trace_id"),
            "verification_issue",
            name="verifier",
            payload=dict(issue),
        )
    result: dict[str, Any] = {
        "verification_result": verification,
        "verified_evidence": verified_evidence,
        "supervisor_route": "answer_generator",
        "supervisor_reason": verification["retry_reason"] or "结果核验完成",
        "supervisor_finalized": False,
    }
    if not verification["needs_retry"]:
        return result
    if verification.get("repair_targets"):
        # §P0-5：问题能落到具体执行单元时只做局部修复，计划、已完成步骤的报告与
        # 本轮证据全部交给 Repair Router 处理，这里不重置任何执行痕迹（P0-6）。
        result["supervisor_route"] = "repair"
        result["repair_count"] = repair_count + 1
        return result
    if str(state.get("execution_mode") or "") == "simple":
        # 简单路径没有 Planner，也不允许整体重排（§P1-1、§二 问题 3）：直接基于已核验
        # 证据作答，剩下的核验问题由 Answer Generator 转成「需要补充的信息」。
        record_trace_event(
            state.get("trace_id"),
            "replan_skipped",
            name="verifier",
            payload={"execution_mode": "simple", "retry_reason": verification["retry_reason"]},
        )
        record_replan(skipped=True)
        return result
    result.update(_retry_updates(state))
    result["verification_result"] = verification
    result["verified_evidence"] = verified_evidence
    result["supervisor_route"] = "replan"
    result["replan_retry_count"] = retry_count + 1
    # 仅用于读取旧 checkpoint/旧监控字段，新逻辑不得用它控制传输重试。
    result["verifier_retry_count"] = retry_count + 1
    # §二十五：repair 与 replan 的比值是「不默认整体重跑」（§二十六）的验收口径。
    record_replan()
    return result
