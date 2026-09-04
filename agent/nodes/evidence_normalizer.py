"""Evidence Normalizer：把工具返回的原始检索结果收敛成规范化证据。

P0-3 的固定顺序：清洗 → 规范化 → 去重 → 有效性过滤 → 相关性过滤 → TopK → State。
所有检索证据只能经由本模块进入 ``retrieved_laws`` / ``retrieved_cases``。
"""
from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.messages import ToolMessage

from agent.evidence import (
    CaseEvidence,
    LawEvidence,
    assign_ref_ids,
    case_evidence_key,
    evidence_payload,
    law_evidence_key,
    normalize_case_evidence,
    normalize_law_evidence,
)
from agent.node_utils import record_trace_event
from agent.state import AgentState, merge_retrieved_cases, merge_retrieved_laws
from services.workflow_metrics import record_evidence_normalized

_LAW_SOURCE_TYPES = {"local_rag", "delilegal_law"}
_CASE_SOURCE_TYPES = {"delilegal_case"}
_RETRIEVAL_SOURCE_TYPES = _LAW_SOURCE_TYPES | _CASE_SOURCE_TYPES


def _top_k(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _min_relevance() -> float:
    try:
        return float(os.getenv("EVIDENCE_MIN_RELEVANCE", "0"))
    except (TypeError, ValueError):
        return 0.0


def _flatten_law_results(results: list[Any]) -> list[dict[str, Any]]:
    """把得理法规结果的 ``relevant_articles`` 拆成逐条法条。"""
    items: list[dict[str, Any]] = []
    for law in results:
        if not isinstance(law, dict):
            continue
        articles = law.get("relevant_articles") or []
        for article in articles:
            if not isinstance(article, dict):
                continue
            items.append({
                "law_name": law.get("law_name") or law.get("title", ""),
                "article_no": article.get("article_no", ""),
                "content": article.get("content", ""),
                "source_type": "delilegal_law",
                "source_id": law.get("id", ""),
                "title": law.get("title", ""),
                "issued_no": law.get("issued_no"),
                "publisher_name": law.get("publisher_name"),
                "publish_date": law.get("publish_date"),
                "active_date": law.get("active_date"),
                "timeliness_name": law.get("timeliness_name"),
                "level_name": law.get("level_name"),
                "score": law.get("score"),
            })
    return items


class _RawBatch:
    """一次归一化扫描收集到的原始检索结果。"""

    def __init__(self) -> None:
        self.laws: list[dict[str, Any]] = []
        self.cases: list[dict[str, Any]] = []
        self.retrieval_attempted = False
        self.evidence_found = False

    def absorb(self, payload: Any) -> None:
        if isinstance(payload, list):
            self.laws.extend(item for item in payload if isinstance(item, dict))
            return
        if not isinstance(payload, dict):
            return
        source_type = str(payload.get("source_type") or "")
        if source_type in _RETRIEVAL_SOURCE_TYPES or "evidence_insufficient" in payload:
            self.retrieval_attempted = True
            self.evidence_found = self.evidence_found or payload.get("status") == "found"
        results = payload.get("results")
        if isinstance(results, list):
            if source_type == "delilegal_law":
                self.laws.extend(_flatten_law_results(results))
            elif source_type in _CASE_SOURCE_TYPES:
                self.cases.extend(item for item in results if isinstance(item, dict))
            else:
                self.laws.extend(item for item in results if isinstance(item, dict))
        if isinstance(payload.get("relevant_laws"), list):
            self.laws.extend(item for item in payload["relevant_laws"] if isinstance(item, dict))
        if isinstance(payload.get("law_a"), dict) and isinstance(payload.get("law_b"), dict):
            for key in ("law_a", "law_b"):
                self.laws.extend(
                    item for item in payload[key].get("articles", []) or [] if isinstance(item, dict)
                )


def _message_marker(message: ToolMessage, index: int) -> str:
    for value in (getattr(message, "id", ""), getattr(message, "tool_call_id", "")):
        if value:
            return str(value)
    return f"index:{index}"


def _collect_raw(state: AgentState) -> tuple[_RawBatch, list[str]]:
    """只扫描本轮尚未归一化过的 ToolMessage，避免重复计入同一批检索结果。"""
    batch = _RawBatch()
    consumed = set(state.get("normalized_tool_message_ids", []) or [])
    markers: list[str] = []
    for index, message in enumerate(state.get("messages", []) or []):
        if not isinstance(message, ToolMessage):
            continue
        marker = _message_marker(message, index)
        if marker in consumed:
            continue
        markers.append(marker)
        payload: Any = message.content
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                continue
        batch.absorb(payload)
    return batch, markers


def _normalize_laws(raw_items: list[dict[str, Any]]) -> tuple[list[LawEvidence], int, int]:
    """规范化 → 去重 → 有效性过滤 → 相关性过滤 → TopK。"""
    seen: dict[str, LawEvidence] = {}
    duplicates = 0
    dropped = 0
    threshold = _min_relevance()
    for raw in raw_items:
        evidence = normalize_law_evidence(raw)
        if evidence is None:
            dropped += 1
            continue
        if not evidence.content and not evidence.article_no:
            dropped += 1
            continue
        if evidence.validity == "invalid":
            dropped += 1
            continue
        if threshold > 0 and evidence.relevance_score and evidence.relevance_score < threshold:
            dropped += 1
            continue
        key = law_evidence_key(evidence)
        if key in seen:
            duplicates += 1
            if evidence.relevance_score > seen[key].relevance_score:
                seen[key] = evidence
            continue
        seen[key] = evidence
    ranked = sorted(seen.values(), key=lambda item: -item.relevance_score)
    return ranked[: _top_k("EVIDENCE_LAW_TOP_K", 8)], duplicates, dropped


def _normalize_cases(raw_items: list[dict[str, Any]]) -> tuple[list[CaseEvidence], int, int]:
    seen: dict[str, CaseEvidence] = {}
    duplicates = 0
    dropped = 0
    for raw in raw_items:
        evidence = normalize_case_evidence(raw)
        if evidence is None:
            dropped += 1
            continue
        key = case_evidence_key(evidence)
        if key in seen:
            duplicates += 1
            if evidence.relevance_score > seen[key].relevance_score:
                seen[key] = evidence
            continue
        seen[key] = evidence
    ranked = sorted(seen.values(), key=lambda item: -item.relevance_score)
    return ranked[: _top_k("EVIDENCE_CASE_TOP_K", 5)], duplicates, dropped


def normalize_evidence(state: AgentState) -> dict[str, Any]:
    """Evidence Normalizer 节点：把新的工具观测收敛成规范化、去重、有界的证据。"""
    batch, markers = _collect_raw(state)

    laws, law_duplicates, law_dropped = _normalize_laws(batch.laws)
    cases, case_duplicates, case_dropped = _normalize_cases(batch.cases)

    previous_laws = list(state.get("retrieved_laws", []) or [])
    previous_cases = list(state.get("retrieved_cases", []) or [])
    previous_keys = {law_evidence_key(item) for item in previous_laws}
    previous_keys.update(case_evidence_key(item) for item in previous_cases)

    merged_laws = merge_retrieved_laws(previous_laws, [evidence_payload(item) for item in laws])
    merged_cases = merge_retrieved_cases(previous_cases, [evidence_payload(item) for item in cases])
    assign_ref_ids(merged_laws, "law")
    assign_ref_ids(merged_cases, "case")

    merged_keys = {law_evidence_key(item) for item in merged_laws}
    merged_keys.update(case_evidence_key(item) for item in merged_cases)
    evidence_gain = len(merged_keys - previous_keys)

    stats = {
        "raw_law_count": len(batch.laws),
        "raw_case_count": len(batch.cases),
        "unique_law_count": len(merged_laws),
        "unique_case_count": len(merged_cases),
        "duplicate_count": law_duplicates + case_duplicates,
        "dropped_count": law_dropped + case_dropped,
        "evidence_gain": evidence_gain,
        "normalized_messages": len(markers),
    }

    result: dict[str, Any] = {"evidence_stats": stats, "evidence_gain": evidence_gain}
    if markers:
        result["normalized_tool_message_ids"] = markers
    if merged_laws:
        result["retrieved_laws"] = merged_laws
    if merged_cases:
        result["retrieved_cases"] = merged_cases
    if merged_laws or merged_cases:
        result["evidence_insufficient"] = False
    elif batch.retrieval_attempted:
        result["evidence_insufficient"] = not batch.evidence_found

    if markers:
        record_trace_event(
            state.get("trace_id"),
            "evidence_normalized",
            name="evidence_normalizer",
            payload=stats,
        )
        # §二十五：只有真正处理了新观测才上报，否则空跑一次会把「进模型证据条数」
        # 的分布拉平（§P1-7 的验收看的是每批次实际留下的条数）。
        record_evidence_normalized(
            law_count=len(merged_laws),
            case_count=len(merged_cases),
            dropped_count=law_dropped + case_dropped,
            evidence_gain=evidence_gain,
        )
    if law_duplicates or case_duplicates:
        record_trace_event(
            state.get("trace_id"),
            "evidence_deduplicated",
            name="evidence_normalizer",
            payload={
                "law_duplicates": law_duplicates,
                "case_duplicates": case_duplicates,
                "dropped_count": law_dropped + case_dropped,
            },
        )
    return result
