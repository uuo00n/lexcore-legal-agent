"""多路召回结果融合算法。"""
from __future__ import annotations

from collections.abc import Sequence

from services.rag.interfaces import LawChunk

ScoredResults = Sequence[tuple[LawChunk, float]]


def reciprocal_rank_fusion(
    result_sets: Sequence[ScoredResults],
    k: int = 60,
) -> list[LawChunk]:
    """用 RRF 融合任意数量的有序召回结果。"""
    if k < 0:
        raise ValueError("RRF 的 k 不能小于 0")
    scores: dict[str, float] = {}
    documents: dict[str, LawChunk] = {}
    for results in result_sets:
        for rank, (document, _) in enumerate(results, start=1):
            scores[document.chunk_id] = (
                scores.get(document.chunk_id, 0.0) + 1.0 / (k + rank)
            )
            documents[document.chunk_id] = document
    ordered_ids = sorted(scores, key=scores.get, reverse=True)
    return [documents[document_id] for document_id in ordered_ids]


def append_unique_results(
    target: list[tuple[LawChunk, float]],
    additions: ScoredResults,
) -> None:
    """按 chunk_id 合并结果，并保留先命中结果的排序优势。"""
    seen = {document.chunk_id for document, _ in target}
    for document, score in additions:
        if document.chunk_id not in seen:
            target.append((document, score))
            seen.add(document.chunk_id)
