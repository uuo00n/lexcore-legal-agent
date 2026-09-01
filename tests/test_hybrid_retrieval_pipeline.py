"""Hybrid Retrieval 双路召回、融合、精排与降级测试。"""
from __future__ import annotations

import pytest

from services.rag.interfaces import DocumentResult, LawChunk
from services.rag.retriever import HybridRetriever
from services.local_legal_retriever import LocalLegalRetriever


def _chunk(number: int) -> LawChunk:
    return LawChunk(
        law_name="测试法",
        hierarchy="第一章",
        article_no=f"第{number}条",
        content=f"第{number}条 测试内容{number}",
        chunk_id=f"测试法_第{number}条",
    )


class _Retriever:
    def __init__(self, results=None, error: Exception | None = None):
        self.results = list(results or [])
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def retrieve(self, query: str, top_k: int):
        self.calls.append((query, top_k))
        if self.error:
            raise self.error
        return self.results[:top_k]


class _Reranker:
    def __init__(self):
        self.calls: list[tuple[str, list[LawChunk], int]] = []

    def rerank(self, query: str, chunks: list[LawChunk], top_n: int):
        self.calls.append((query, chunks, top_n))
        return [
            DocumentResult(chunk, 0.99 - index * 0.1)
            for index, chunk in enumerate(reversed(chunks[:top_n]))
        ]


@pytest.fixture(autouse=True)
def _disable_query_enhancement(monkeypatch):
    monkeypatch.setenv("HYDE_ENABLED", "false")


def test_hybrid_pipeline_uses_independent_top_k_and_traces_all_scores(monkeypatch):
    first, second, third = _chunk(1), _chunk(2), _chunk(3)
    vector = _Retriever([
        DocumentResult(first, 0.91),
        DocumentResult(second, 0.82),
        DocumentResult(third, 0.73),
    ])
    bm25 = _Retriever([
        DocumentResult(second, 8.5),
        DocumentResult(third, 7.1),
    ])
    reranker = _Reranker()
    events: list[tuple[str, list[DocumentResult], dict]] = []
    monkeypatch.setattr(
        "services.rag.retriever._record_retrieval_event",
        lambda _trace_id, event_type, results, **details: events.append(
            (event_type, list(results), details)
        ),
    )
    monkeypatch.setenv("RETRIEVAL_VECTOR_TOP_K", "2")
    monkeypatch.setenv("RETRIEVAL_BM25_TOP_K", "1")
    monkeypatch.setenv("RETRIEVAL_FINAL_TOP_K", "1")

    retriever = HybridRetriever(
        semantic=vector,
        keyword=bm25,
        reranker=reranker,
    )
    results = retriever.retrieve_with_scores("劳动法处罚标准", trace_id="trace-1")

    assert vector.calls == [("劳动法处罚标准", 2)]
    assert bm25.calls == [("劳动法处罚标准", 1)]
    assert len(results) == 1
    assert results[0].score == pytest.approx(0.99)
    assert reranker.calls[0][2] == 1
    assert [event[0] for event in events] == [
        "vector_hits",
        "bm25_hits",
        "fused_hits",
        "reranker_hits",
    ]
    assert [score for _chunk, score in events[0][1]] == [0.91, 0.82]
    assert [score for _chunk, score in events[1][1]] == [8.5]
    fused_scores = {
        chunk.chunk_id: score for chunk, score in events[2][1]
    }
    assert fused_scores[second.chunk_id] == pytest.approx(1 / 62 + 1 / 61)
    assert fused_scores[first.chunk_id] == pytest.approx(1 / 61)
    assert [score for _chunk, score in events[3][1]] == [pytest.approx(0.99)]


def test_pipeline_without_reranker_returns_rrf_scores():
    first, second = _chunk(1), _chunk(2)
    retriever = HybridRetriever(
        semantic=_Retriever([DocumentResult(first, 0.8)]),
        keyword=_Retriever([DocumentResult(second, 4.2)]),
        reranker=None,
        final_top_k=2,
        score_threshold=0.9,
    )

    scored = retriever.retrieve_with_scores("劳动法处罚标准")
    documents = retriever.retrieve("劳动法处罚标准")

    assert len(scored) == 2
    assert all(result.score > 0 for result in scored)
    assert documents == [result.document for result in scored]


def test_vector_failure_falls_back_to_bm25(monkeypatch):
    bm25_chunk = _chunk(2)
    events = []
    monkeypatch.setattr(
        "services.rag.retriever._record_retrieval_event",
        lambda _trace_id, event_type, results, **details: events.append(
            (event_type, list(results), details)
        ),
    )
    retriever = HybridRetriever(
        semantic=_Retriever(error=RuntimeError("vector unavailable")),
        keyword=_Retriever([DocumentResult(bm25_chunk, 6.2)]),
        reranker=None,
    )

    results = retriever.retrieve_with_scores("劳动法处罚标准", trace_id="trace-fallback")

    assert results == [DocumentResult(bm25_chunk, 6.2)]
    assert events[0][0] == "vector_hits"
    assert events[0][2]["error_type"] == "RuntimeError"
    assert events[2][0] == "fused_hits"
    assert events[2][2]["mode"] == "bm25_fallback"


def test_bm25_failure_falls_back_to_vector():
    vector_chunk = _chunk(1)
    retriever = HybridRetriever(
        semantic=_Retriever([DocumentResult(vector_chunk, 0.88)]),
        keyword=_Retriever(error=RuntimeError("bm25 unavailable")),
        reranker=None,
    )

    results = retriever.retrieve_with_scores("劳动法处罚标准")

    assert results == [DocumentResult(vector_chunk, 0.88)]


def test_local_retriever_forwards_trace_id():
    seen = {}

    class _TraceableRetriever:
        def retrieve_with_scores(self, query: str, top_k: int, trace_id: str):
            seen.update(query=query, top_k=top_k, trace_id=trace_id)
            return []

    adapter = LocalLegalRetriever(_TraceableRetriever())

    assert adapter.search("劳动法", top_k=3, trace_id="trace-16") == []
    assert seen == {
        "query": "劳动法",
        "top_k": 3,
        "trace_id": "trace-16",
    }
