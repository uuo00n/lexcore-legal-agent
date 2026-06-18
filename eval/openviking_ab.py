"""A/B evaluation for real OpenViking resource-scoped retrieval."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote
import re

from eval.context_ab import ExpectedContext, infer_expected_context
from eval.metrics import RetrievalMetrics, aggregate_metrics, compute_retrieval_metrics
from services.openviking_client import OpenVikingMatch


def run_openviking_ab_eval(
    dataset: list[dict[str, Any]],
    *,
    retriever: Any,
    openviking_client: Any,
    top_k: int = 5,
    limit: int | None = None,
    openviking_limit: int = 5,
    candidate_k: int | None = None,
    target_uri: str = "viking://resources/laws",
) -> dict[str, Any]:
    """Compare baseline HybridRetriever with OpenViking resource-scoped rerank."""
    selected = dataset[:limit] if limit else dataset
    baseline_metrics: list[RetrievalMetrics] = []
    openviking_metrics: list[RetrievalMetrics] = []
    details: list[dict[str, Any]] = []
    resource_hits: list[float] = []
    match_counts: list[float] = []

    scoped_candidate_k = candidate_k or max(top_k * 6, openviking_limit, top_k)

    for item in selected:
        question = item["question"]
        gt_contexts = item["ground_truth_contexts"]
        acceptable_contexts = item.get("acceptable_contexts") or gt_contexts

        if item.get("corpus_status", "in_corpus") == "out_of_corpus":
            details.append({
                "question": question,
                "corpus_status": "out_of_corpus",
                "baseline_retrieved_ids": [],
                "openviking_retrieved_ids": [],
            })
            continue

        baseline_chunks = retriever.retrieve(question, top_k=top_k)
        baseline_ids = _chunk_ids(baseline_chunks)
        baseline = compute_retrieval_metrics(baseline_ids, gt_contexts, acceptable_contexts)
        baseline_metrics.append(baseline)

        matches = openviking_client.find(
            question,
            target_uri=target_uri,
            context_type="resource",
            limit=openviking_limit,
            level=[0, 1, 2],
        )
        candidate_chunks = retriever.retrieve(question, top_k=scoped_candidate_k)
        scoped_chunks = rerank_chunks_by_openviking_scope(candidate_chunks, matches)[:top_k]
        scoped_ids = _chunk_ids(scoped_chunks)
        scoped = compute_retrieval_metrics(scoped_ids, gt_contexts, acceptable_contexts)
        openviking_metrics.append(scoped)

        expected = infer_expected_context(item)
        resource_hit = _resource_hit(matches, expected)
        if resource_hit is not None:
            resource_hits.append(1.0 if resource_hit else 0.0)
        match_counts.append(float(len(matches)))

        details.append({
            "question": question,
            "corpus_status": "in_corpus",
            "baseline_retrieved_ids": baseline_ids,
            "openviking_candidate_ids": _chunk_ids(candidate_chunks),
            "openviking_retrieved_ids": scoped_ids,
            "openviking_matches": [_match_to_dict(match) for match in matches],
            "baseline_hit": baseline.hit,
            "openviking_hit": scoped.hit,
            "baseline_reciprocal_rank": baseline.reciprocal_rank,
            "openviking_reciprocal_rank": scoped.reciprocal_rank,
            "baseline_precision": baseline.precision,
            "openviking_precision": scoped.precision,
            "baseline_recall": baseline.recall,
            "openviking_recall": scoped.recall,
            "resource_hit": resource_hit,
        })

    baseline_aggregated = aggregate_metrics(baseline_metrics)
    openviking_aggregated = aggregate_metrics(openviking_metrics)
    return {
        "mode": "openviking_ab",
        "top_k": top_k,
        "num_queries": len(baseline_metrics),
        "num_total_queries": len(selected),
        "baseline": {
            "name": "hybrid_retriever_raw_query",
            "aggregated": baseline_aggregated,
        },
        "openviking": {
            "name": "real_openviking_resource_article_scoped_rerank",
            "aggregated": openviking_aggregated,
        },
        "delta": _delta(openviking_aggregated, baseline_aggregated),
        "openviking_routing": {
            "resource_hit_rate": _avg(resource_hits),
            "avg_openviking_matches": _avg(match_counts),
            "resource_eval_count": len(resource_hits),
        },
        "details": details,
    }


def rerank_chunks_by_openviking_scope(
    chunks: list[Any],
    matches: list[OpenVikingMatch],
) -> list[Any]:
    """Boost chunks whose exact article or law name appears in OpenViking hits."""
    scope_chunk_ids = _scope_chunk_ids(matches)
    scope_names = _scope_law_names(matches)
    if not scope_chunk_ids and not scope_names:
        return list(chunks)

    chunk_rank = {chunk_id: rank for rank, chunk_id in enumerate(scope_chunk_ids)}
    law_rank = {law_name: rank for rank, law_name in enumerate(scope_names)}

    indexed = list(enumerate(chunks))
    indexed.sort(
        key=lambda pair: (
            _chunk_score(pair[0], pair[1], chunk_rank, law_rank),
            pair[0],
        )
    )
    return [chunk for _, chunk in indexed]


def _chunk_ids(chunks: list[Any]) -> list[str]:
    return [str(getattr(chunk, "chunk_id", "")) for chunk in chunks if getattr(chunk, "chunk_id", "")]


def _chunk_law_name(chunk: Any) -> str:
    law_name = getattr(chunk, "law_name", "")
    if law_name:
        return str(law_name)
    chunk_id = str(getattr(chunk, "chunk_id", ""))
    return chunk_id.split("_", 1)[0] if "_" in chunk_id else chunk_id


def _scope_law_names(matches: list[OpenVikingMatch]) -> set[str]:
    names: set[str] = set()
    for match in matches:
        if match.context_type and match.context_type != "resource":
            continue
        name = _law_name_from_uri(match.uri)
        if name:
            names.add(name)
    return names


_ARTICLE_NO_PATTERN = re.compile(r"第[零一二三四五六七八九十百千万\d]+条(?:之[零一二三四五六七八九十百千万\d]+)?")
_CHUNK_ID_SUFFIX_PATTERN = re.compile(
    rf"(?P<law>.+)_(?P<article>{_ARTICLE_NO_PATTERN.pattern})$"
)


def _chunk_score(
    original_rank: int,
    chunk: Any,
    chunk_rank: dict[str, int],
    law_rank: dict[str, int],
) -> float:
    """Bound OpenViking boosts so near-miss articles do not dominate reranker order."""
    score = float(original_rank)
    chunk_id = str(getattr(chunk, "chunk_id", ""))
    if chunk_id in chunk_rank:
        score -= 1.25 / (chunk_rank[chunk_id] + 1)

    law_name = _chunk_law_name(chunk)
    if law_name in law_rank:
        score -= 0.25 / (law_rank[law_name] + 1)

    return score


def _scope_chunk_ids(matches: list[OpenVikingMatch]) -> list[str]:
    chunk_ids: list[str] = []
    seen: set[str] = set()
    for match in matches:
        if match.context_type and match.context_type != "resource":
            continue
        for chunk_id in _chunk_ids_from_match(match):
            if chunk_id and chunk_id not in seen:
                chunk_ids.append(chunk_id)
                seen.add(chunk_id)
    return chunk_ids


def _chunk_ids_from_match(match: OpenVikingMatch) -> list[str]:
    from_uri = _chunk_id_from_uri(match.uri)
    if from_uri:
        return [from_uri]

    law_name = _law_name_from_uri(match.uri)
    if not law_name:
        return []

    text = " ".join(
        value
        for value in (
            getattr(match, "abstract", ""),
            getattr(match, "overview", ""),
            getattr(match, "content", ""),
        )
        if value
    )
    return [f"{law_name}_{article}" for article in _article_numbers_from_text(text)]


def _chunk_id_from_uri(uri: str) -> str:
    if not uri:
        return ""
    parts = _uri_parts(uri)
    if not parts:
        return ""

    stem = Path(parts[-1]).stem
    suffix_match = _CHUNK_ID_SUFFIX_PATTERN.match(stem)
    if suffix_match:
        return stem

    if _ARTICLE_NO_PATTERN.fullmatch(stem) and len(parts) >= 2:
        law_name = Path(parts[-2]).stem
        if law_name:
            return f"{law_name}_{stem}"

    return ""


def _article_numbers_from_text(text: str) -> list[str]:
    articles: list[str] = []
    seen: set[str] = set()
    for match in _ARTICLE_NO_PATTERN.finditer(text):
        article = match.group(0)
        if article not in seen:
            articles.append(article)
            seen.add(article)
    return articles


def _law_name_from_uri(uri: str) -> str:
    if not uri:
        return ""
    parts = _uri_parts(uri)
    if not parts:
        return ""

    stem = Path(parts[-1]).stem
    suffix_match = _CHUNK_ID_SUFFIX_PATTERN.match(stem)
    if suffix_match:
        return suffix_match.group("law")

    if _ARTICLE_NO_PATTERN.fullmatch(stem) and len(parts) >= 2:
        return Path(parts[-2]).stem

    return stem.split("_", 1)[-1]


def _uri_parts(uri: str) -> list[str]:
    decoded = unquote(uri.rstrip("/"))
    if not decoded:
        return []
    return [part for part in decoded.split("/") if part and part != "viking:"]


def _resource_hit(matches: list[OpenVikingMatch], expected: ExpectedContext) -> bool | None:
    if not expected.resource_uris:
        return None
    matched_uris = {match.uri for match in matches if match.context_type == "resource"}
    return any(
        matched == expected_uri or matched.startswith(expected_uri)
        for matched in matched_uris
        for expected_uri in expected.resource_uris
    )


def _match_to_dict(match: OpenVikingMatch) -> dict[str, Any]:
    to_dict = getattr(match, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return {
        "uri": getattr(match, "uri", ""),
        "context_type": getattr(match, "context_type", ""),
        "score": getattr(match, "score", 0.0),
        "abstract": getattr(match, "abstract", ""),
    }


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _delta(after: dict[str, float], before: dict[str, float]) -> dict[str, float]:
    keys = sorted(set(after) | set(before))
    return {key: round(after.get(key, 0.0) - before.get(key, 0.0), 6) for key in keys}
