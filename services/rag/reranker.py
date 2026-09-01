"""Cross-Encoder 精排器。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from services.rag.interfaces import DocumentResult, LawChunk

_reranker_model = None


def _get_reranker():
    """延迟加载并复用 Cross-Encoder 模型。"""
    global _reranker_model
    if _reranker_model is None:
        from sentence_transformers import CrossEncoder

        model_name = os.getenv("RERANKER_MODEL", "models/bge-reranker-base")
        model_path = Path(model_name)
        if model_path.exists():
            model_name = str(model_path.resolve())
        _reranker_model = CrossEncoder(model_name)
    return _reranker_model


class Reranker:
    """对融合后的少量候选文档进行精排。"""

    def __init__(self, top_n: Optional[int] = None) -> None:
        self._top_n = top_n or int(os.getenv("RERANKER_TOP_N", "5"))

    def rerank(
        self,
        query: str,
        chunks: list[LawChunk],
        top_n: Optional[int] = None,
    ) -> list[DocumentResult]:
        if not chunks:
            return []
        scores = _get_reranker().predict(
            [(query, chunk.content) for chunk in chunks]
        )
        ranked = sorted(
            zip(chunks, scores),
            key=lambda item: item[1],
            reverse=True,
        )
        limit = top_n or self._top_n
        return [
            DocumentResult(chunk, float(score))
            for chunk, score in ranked[:limit]
        ]
