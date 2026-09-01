"""本地 DOC 法律 RAG 的稳定适配层。"""
from __future__ import annotations

from typing import Any

from services.rag.retriever import get_retriever


class LocalLegalRetriever:
    """隔离工具层与 Chroma/BM25/RRF/Reranker 的具体实现。"""

    def __init__(self, retriever: Any | None = None) -> None:
        self._retriever = retriever or get_retriever()

    @property
    def score_threshold(self) -> float:
        return float(getattr(self._retriever, "score_threshold", 0.3))

    def search(self, query: str, top_k: int = 5) -> list[tuple[Any, float | None]]:
        if hasattr(self._retriever, "retrieve_with_scores"):
            return list(self._retriever.retrieve_with_scores(query, top_k=top_k))
        return [(item, None) for item in self._retriever.retrieve(query, top_k=top_k)]
