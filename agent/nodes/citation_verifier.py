"""确定性 Citation Verifier：只用 Python 规则核验引用，绝不调用模型（§十四、P0-1）。

核验维度全部可判定：evidence_id / source_id 是否存在、条号是否匹配、
引用是否来自本轮 Evidence、法规时效性、重复引用、canonical 法规是否一致。
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent.evidence import (
    canonical_article_no,
    canonical_law_id,
    canonical_law_name,
    case_evidence_key,
    law_evidence_key,
    law_validity,
)
from agent.reports import report_agent_name
from agent.state import AgentState, CitationCheck, VerificationIssue, VerifiedEvidence

LAW_CITATION_RE = re.compile(
    r"《([^》]+)》\s*"
    r"(第[一二三四五六七八九十百千万亿零〇两\d]+条(?:之[一二三四五六七八九十百千万亿零〇两\d]+)?)"
)
CASE_NO_RE = re.compile(r"[（(][12]\d{3}[）)][^，。；;\s]{1,40}?号")

_LAW_HINT_FIELDS = ("law_name", "article_no", "timeliness_name", "canonical_law_id")
_CASE_HINT_FIELDS = ("case_id", "case_no", "case_name", "case_number")
_VALIDITY_FIELDS = ("timeliness_name", "validity_status", "effectiveness", "status")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _compact(value: Any) -> str:
    return re.sub(r"[\s（）()]", "", _text(value)).lower()


def evidence_validity(item: Mapping[str, Any]) -> str:
    """读取证据时效性；旧 checkpoint 与未归一化数据回退到原始时效字段推断。"""
    declared = _text(item.get("validity"))
    if declared:
        return declared
    return law_validity(*(item.get(field) for field in _VALIDITY_FIELDS))


def looks_like_case(item: Mapping[str, Any]) -> bool:
    source_type = _text(item.get("source_type")).lower()
    return "case" in source_type or any(item.get(key) for key in _CASE_HINT_FIELDS)


def looks_like_law(item: Mapping[str, Any]) -> bool:
    if looks_like_case(item):
        return False
    source_type = _text(item.get("source_type")).lower()
    return "law" in source_type or "rag" in source_type or any(
        item.get(key) for key in _LAW_HINT_FIELDS
    )


def law_label(item: Mapping[str, Any]) -> str:
    name = _text(item.get("display_law_name") or item.get("law_name") or item.get("title")) or "未知法规"
    return f"《{name}》{_text(item.get('article_no'))}"


def case_label(item: Mapping[str, Any]) -> str:
    return (
        _text(item.get("case_no"))
        or _text(item.get("case_name"))
        or _text(item.get("title"))
        or _text(item.get("source_id") or item.get("case_id"))
        or "未知案例"
    )


@dataclass
class EvidenceIndex:
    """本轮可引用证据的多口径索引；所有匹配都必须命中其中之一。"""

    laws: list[dict[str, Any]] = field(default_factory=list)
    cases: list[dict[str, Any]] = field(default_factory=list)
    law_by_evidence_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    law_by_canonical: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    law_by_source: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    law_by_law_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    case_by_evidence_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    case_by_source: dict[str, dict[str, Any]] = field(default_factory=dict)
    case_by_no: dict[str, dict[str, Any]] = field(default_factory=dict)
    case_by_name: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def has_evidence(self) -> bool:
        return bool(self.laws or self.cases)


def _law_identity(item: Mapping[str, Any]) -> tuple[str, str, str]:
    """返回 ``(canonical_law_id, canonical_article_no, source_id)``；兼容未归一化的旧数据。"""
    law_id = _text(item.get("canonical_law_id")) or canonical_law_id(
        item.get("display_law_name") or item.get("law_name") or item.get("title")
    )
    article = _text(item.get("canonical_article_no")) or canonical_article_no(item.get("article_no"))
    source_id = _text(item.get("source_id") or item.get("id"))
    return law_id, article, source_id


def build_evidence_index(state: AgentState) -> EvidenceIndex:
    """基于 State 中已归一化的证据建立索引。"""
    index = EvidenceIndex()
    for raw in state.get("retrieved_laws", []) or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        index.laws.append(item)
        law_id, article, source_id = _law_identity(item)
        evidence_id = _text(item.get("evidence_id"))
        if evidence_id:
            index.law_by_evidence_id.setdefault(evidence_id, item)
        if law_id:
            index.law_by_canonical.setdefault((law_id, article), item)
            index.law_by_law_id.setdefault(law_id, item)
        if source_id:
            index.law_by_source.setdefault((source_id, article), item)
    for raw in state.get("retrieved_cases", []) or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        index.cases.append(item)
        evidence_id = _text(item.get("evidence_id"))
        if evidence_id:
            index.case_by_evidence_id.setdefault(evidence_id, item)
        for key in ("source_id", "case_id", "id"):
            value = _text(item.get(key))
            if value:
                index.case_by_source.setdefault(value, item)
        case_no = _compact(item.get("case_no") or item.get("case_number"))
        if case_no:
            index.case_by_no.setdefault(case_no, item)
        name = _compact(item.get("case_name") or item.get("title"))
        if name:
            index.case_by_name.setdefault(name, item)
    return index


def _law_conflicts(law_id: str, article: str, evidence: Mapping[str, Any]) -> bool:
    """判断引用写出的法规名／条号是否与按 ID 命中的证据矛盾。"""
    evidence_law, evidence_article, _ = _law_identity(evidence)
    if article and evidence_article and article != evidence_article:
        return True
    return bool(law_id and evidence_law and law_id != evidence_law)


def match_law(citation: Mapping[str, Any], index: EvidenceIndex) -> tuple[dict[str, Any] | None, str]:
    """按 ref_id → evidence_id → (source_id, 条号) → canonical(法规, 条号) 匹配本轮法条。

    ``ref_id`` / ``evidence_id`` 命中后仍要校验可见的法规名与条号：Agent 内部按
    ``law_001`` 引用，但用户看到的是正文里写出的引用，两者矛盾时不能算核验通过。
    """
    mismatch = False
    law_id, article, source_id = _law_identity(citation)

    def confirm(item: dict[str, Any], reason: str) -> tuple[dict[str, Any], str] | None:
        nonlocal mismatch
        if _law_conflicts(law_id, article, item):
            mismatch = True
            return None
        return item, reason

    ref_id = _text(citation.get("ref_id"))
    if ref_id:
        for item in index.laws:
            if _text(item.get("ref_id")) == ref_id:
                confirmed = confirm(item, "ref_id")
                if confirmed:
                    return confirmed
                break
    evidence_id = _text(citation.get("evidence_id"))
    if evidence_id and evidence_id in index.law_by_evidence_id:
        confirmed = confirm(index.law_by_evidence_id[evidence_id], "evidence_id")
        if confirmed:
            return confirmed
    if source_id and (source_id, article) in index.law_by_source:
        confirmed = confirm(index.law_by_source[(source_id, article)], "source_id")
        if confirmed:
            return confirmed
    if law_id and (law_id, article) in index.law_by_canonical:
        return index.law_by_canonical[(law_id, article)], "canonical_law"
    if law_id and not article and law_id in index.law_by_law_id:
        return index.law_by_law_id[law_id], "canonical_law_name"
    if mismatch:
        return None, "citation_mismatch"
    if evidence_id:
        return None, "evidence_id_not_found"
    if law_id and law_id in index.law_by_law_id:
        return None, "article_not_in_evidence"
    return None, "law_not_in_evidence"


def match_case(citation: Mapping[str, Any], index: EvidenceIndex) -> tuple[dict[str, Any] | None, str]:
    """按 ref_id → evidence_id → 来源标识 → 案号 → 案件名称匹配本轮类案。

    与法条同理：按 ID 命中后若引用写出的案号与证据不符，按「引用不一致」处理。
    """
    mismatch = False
    case_no = _compact(citation.get("case_no") or citation.get("case_number"))

    def confirm(item: dict[str, Any], reason: str) -> tuple[dict[str, Any], str] | None:
        nonlocal mismatch
        if case_no and case_no != _compact(item.get("case_no") or item.get("case_number")):
            mismatch = True
            return None
        return item, reason

    ref_id = _text(citation.get("ref_id"))
    if ref_id:
        for item in index.cases:
            if _text(item.get("ref_id")) == ref_id:
                confirmed = confirm(item, "ref_id")
                if confirmed:
                    return confirmed
                break
    evidence_id = _text(citation.get("evidence_id"))
    if evidence_id and evidence_id in index.case_by_evidence_id:
        confirmed = confirm(index.case_by_evidence_id[evidence_id], "evidence_id")
        if confirmed:
            return confirmed
    for key in ("source_id", "case_id", "id"):
        value = _text(citation.get(key))
        if value and value in index.case_by_source:
            confirmed = confirm(index.case_by_source[value], "source_id")
            if confirmed:
                return confirmed
            break
    if case_no:
        # 案号是案例的权威标识：给出了案号却匹配不上，不得再退回名称匹配，
        # 否则「真实案件名 + 编造案号」的引用会被误判为已核验。
        if case_no in index.case_by_no:
            return index.case_by_no[case_no], "case_no"
        return None, "citation_mismatch" if mismatch else "case_no_not_in_evidence"
    name = _compact(citation.get("case_name") or citation.get("title"))
    if name and name in index.case_by_name:
        return index.case_by_name[name], "case_name"
    if mismatch:
        return None, "citation_mismatch"
    return None, "case_not_in_evidence"


_FAIL_REASONS = {
    "evidence_id_not_found": "引用的 evidence_id 不在本轮检索证据中",
    "article_not_in_evidence": "本轮检索到该法规，但未检索到被引用的条文",
    "law_not_in_evidence": "本轮检索结果中不存在该法规",
    "case_no_not_in_evidence": "本轮检索结果中不存在该案号",
    "case_not_in_evidence": "本轮检索结果中不存在该案例",
    "citation_mismatch": "引用标识指向的证据与写出的法规／案号不一致",
    "obsolete": "被引用法规已失效或废止",
}


def report_text(report: Mapping[str, Any]) -> str:
    """报告正文（不含 sources）的稳定文本形态，用于扫描正文中的引用。"""
    content = {key: value for key, value in report.items() if key != "sources"}
    return json.dumps(content, ensure_ascii=False, default=str)


def law_citation(evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "citation_id": _text(evidence.get("evidence_id")),
        "kind": "law",
        "evidence_id": _text(evidence.get("evidence_id")),
        "ref_id": _text(evidence.get("ref_id")),
        "source_type": _text(evidence.get("source_type")),
        "source_id": _text(evidence.get("source_id")),
        "law_name": _text(evidence.get("display_law_name") or evidence.get("law_name")),
        "title": _text(evidence.get("title") or evidence.get("law_name")),
        "article_no": _text(evidence.get("article_no")),
        "content": _text(evidence.get("content")),
    }


def case_citation(evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "citation_id": _text(evidence.get("evidence_id")),
        "kind": "case",
        "evidence_id": _text(evidence.get("evidence_id")),
        "ref_id": _text(evidence.get("ref_id")),
        "source_type": _text(evidence.get("source_type")),
        "source_id": _text(evidence.get("source_id") or evidence.get("case_id")),
        "title": _text(evidence.get("case_name") or evidence.get("title")),
        "case_no": _text(evidence.get("case_no")),
        "content": _text(evidence.get("summary")),
    }


def evidence_identity(evidence: Mapping[str, Any], kind: str) -> str:
    """引用去重键：优先 ``evidence_id``，否则回退到与 reducer 一致的证据口径。

    同一条证据可能以「《中华人民共和国民法典》第五百七十七条」和
    「《民法典》第五百七十七条」两种写法出现在报告里，按引用标签去重会
    产出两条指向同一证据的引用，因此这里统一按证据本身去重（P0-1、P0-3）。
    """
    evidence_id = _text(evidence.get("evidence_id"))
    if evidence_id:
        return evidence_id
    return case_evidence_key(evidence) if kind == "case" else law_evidence_key(evidence)


class _CitationAuditor:
    """按 (类型, 引用标签) 去重地累积核验明细、通过引用与结构化问题。"""

    def __init__(self, index: EvidenceIndex) -> None:
        self.index = index
        self.checks: list[CitationCheck] = []
        self.issues: list[VerificationIssue] = []
        self.law_citations: dict[str, dict[str, Any]] = {}
        self.case_citations: dict[str, dict[str, Any]] = {}
        self._seen: set[tuple[str, str]] = set()

    def audit(
        self,
        citation: Mapping[str, Any],
        *,
        kind: str,
        report_id: str,
        agent: str,
    ) -> dict[str, Any] | None:
        label = law_label(citation) if kind == "law" else case_label(citation)
        key = (kind, _compact(label))
        if key in self._seen:
            return None
        self._seen.add(key)
        matcher = match_law if kind == "law" else match_case
        evidence, reason = matcher(citation, self.index)
        if evidence is not None and evidence_validity(evidence) == "invalid":
            evidence, reason = None, "obsolete"
        check: CitationCheck = {
            "kind": kind,  # type: ignore[typeddict-item]
            "label": label,
            "report_id": report_id,
            "agent": agent,
            "evidence_id": _text((evidence or {}).get("evidence_id")),
            "ref_id": _text((evidence or {}).get("ref_id")),
            "canonical_law_id": _text((evidence or {}).get("canonical_law_id")),
            "canonical_article_no": _text((evidence or {}).get("canonical_article_no")),
            "verified": evidence is not None,
            "reason": reason,
        }
        self.checks.append(check)
        if evidence is None:
            self.issues.append({
                "type": "obsolete_law" if reason == "obsolete" else "citation_invalid",
                "severity": "blocking",
                "source": "deterministic",
                "agent": agent,
                "step_id": report_id,
                "message": f"{agent or report_id or '专家报告'} 引用了无法核验的{'法条' if kind == 'law' else '案例'}：{label}（{_FAIL_REASONS.get(reason, reason)}）",
            })
            return None
        bucket = self.law_citations if kind == "law" else self.case_citations
        payload = law_citation(evidence) if kind == "law" else case_citation(evidence)
        bucket.setdefault(evidence_identity(evidence, kind), payload)
        return evidence


def verify_citations(state: AgentState) -> tuple[VerifiedEvidence, list[VerificationIssue]]:
    """确定性核验全部报告引用，产出唯一引用真相源 ``verified_evidence``。"""
    index = build_evidence_index(state)
    auditor = _CitationAuditor(index)
    for report in state.get("agent_reports", []) or []:
        if not isinstance(report, Mapping):
            continue
        agent = report_agent_name(report)
        report_id = _text(report.get("task_id") or report.get("step_id") or report.get("report_id"))
        for source in report.get("sources", []) or []:
            if not isinstance(source, Mapping):
                continue
            kind = "case" if looks_like_case(source) else "law" if looks_like_law(source) else ""
            if kind:
                auditor.audit(source, kind=kind, report_id=report_id, agent=agent)
        text = report_text(report)
        for law_name, article_no in LAW_CITATION_RE.findall(text):
            auditor.audit(
                {"law_name": law_name, "article_no": article_no},
                kind="law",
                report_id=report_id,
                agent=agent,
            )
        for case_no in CASE_NO_RE.findall(text):
            auditor.audit(
                {"case_no": case_no},
                kind="case",
                report_id=report_id,
                agent=agent,
            )

    citable_laws = [item for item in index.laws if evidence_validity(item) != "invalid"]
    citations = [*auditor.law_citations.values(), *auditor.case_citations.values()]
    verified_count = sum(1 for check in auditor.checks if check.get("verified"))
    verified_evidence: VerifiedEvidence = {
        "laws": citable_laws,  # type: ignore[typeddict-item]
        "cases": list(index.cases),  # type: ignore[typeddict-item]
        "citations": citations,  # type: ignore[typeddict-item]
        "checks": auditor.checks,
        "evidence_ids": [
            _text(item.get("evidence_id"))
            for item in (*citable_laws, *index.cases)
            if _text(item.get("evidence_id"))
        ],
        "citation_total": len(auditor.checks),
        "citation_verified": verified_count,
        "citation_unsupported": len(auditor.checks) - verified_count,
    }
    return verified_evidence, auditor.issues


def partially_valid_laws(state: AgentState) -> list[dict[str, Any]]:
    """列出时效性为「部分失效」的法规，供核验结果给出风险提示。"""
    return [
        dict(item)
        for item in state.get("retrieved_laws", []) or []
        if isinstance(item, Mapping) and evidence_validity(item) == "partial"
    ]
