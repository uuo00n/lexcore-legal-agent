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

    def search(
        self,
        query: str,
        top_k: int = 5,
        trace_id: str | None = None,
    ) -> list[tuple[Any, float | None]]:
        if hasattr(self._retriever, "retrieve_with_scores"):
            kwargs = {"top_k": top_k}
            if trace_id is not None:
                kwargs["trace_id"] = trace_id
            return list(self._retriever.retrieve_with_scores(query, **kwargs))
        kwargs = {"top_k": top_k}
        if trace_id is not None:
            kwargs["trace_id"] = trace_id
        return [(item, None) for item in self._retriever.retrieve(query, **kwargs)]
