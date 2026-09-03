"""Hybrid Retrieval 双路召回、融合、精排与降级测试。"""
from __future__ import annotations

import pytest

from services.rag.interfaces import (
    DocumentResult,
    LawChunk,
    chunk_search_text,
    is_superseded,
)
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


def _superseded_chunk(number: int) -> LawChunk:
    """构造带「效力标记: 历史版本」的语料块，模拟已失效条文。"""
    chunk = _chunk(number)
    chunk.metadata = {"status": "历史版本（sxx=1）"}
    return chunk


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


class _FixedScoreReranker:
    """按给定顺序返回固定精排分，用于验证阈值裁剪与保底条数。"""

    def __init__(self, scores: list[float]):
        self.scores = scores

    def rerank(self, query: str, chunks: list[LawChunk], top_n: int):
        return [
            DocumentResult(chunk, score)
            for chunk, score in zip(chunks[:top_n], self.scores)
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

    # 查询串必须与其他用例不同：检索缓存 key 由 (query, 检索参数) 构成，
    # 两个参数相同的降级用例共用查询串时，后跑的那个会读到前一个的缓存结果。
    results = retriever.retrieve_with_scores(
        "劳动法处罚标准 向量降级",
        trace_id="trace-fallback",
    )

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

    results = retriever.retrieve_with_scores("劳动法处罚标准 BM25降级")

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


def test_chunk_search_text_prefixes_law_name_and_hierarchy():
    # content 只有「第X条 正文」，索引/BM25/精排必须共用带法名的检索文本，
    # 否则跨法同条号（劳动法第四十一条 vs 劳动合同法第四十一条）无法区分。
    assert chunk_search_text(_chunk(41)) == "测试法 第一章 第41条 测试内容41"

    bare = LawChunk(
        law_name="",
        hierarchy="",
        article_no="第1条",
        content="第1条 正文",
        chunk_id="bare_第1条",
    )
    assert chunk_search_text(bare) == "第1条 正文"


def test_is_superseded_detects_status_and_law_name_markers():
    assert is_superseded(_superseded_chunk(1)) is True
    assert is_superseded(
        LawChunk(
            law_name="合同法_历史版本",
            hierarchy="",
            article_no="第1条",
            content="第1条 正文",
            chunk_id="合同法_历史版本_第1条",
        )
    ) is True
    assert is_superseded(_chunk(1)) is False


def test_superseded_chunks_dropped_from_both_sources_by_default(monkeypatch):
    current_vector, current_bm25 = _chunk(1), _chunk(3)
    events: list[tuple[str, list[DocumentResult], dict]] = []
    monkeypatch.setattr(
        "services.rag.retriever._record_retrieval_event",
        lambda _trace_id, event_type, results, **details: events.append(
            (event_type, list(results), details)
        ),
    )
    retriever = HybridRetriever(
        semantic=_Retriever([
            DocumentResult(current_vector, 0.91),
            DocumentResult(_superseded_chunk(2), 0.95),
        ]),
        keyword=_Retriever([
            DocumentResult(_superseded_chunk(4), 9.1),
            DocumentResult(current_bm25, 6.4),
        ]),
        reranker=None,
        vector_top_k=5,
        bm25_top_k=5,
        final_top_k=5,
    )

    results = retriever.retrieve_with_scores(
        "劳动法处罚标准 历史版本默认剔除",
        trace_id="trace-superseded",
    )

    assert [result.document.chunk_id for result in results] == [
        current_vector.chunk_id,
        current_bm25.chunk_id,
    ]
    fused = next(event for event in events if event[0] == "fused_hits")
    assert fused[2]["superseded_dropped"] == 2


def test_superseded_chunks_kept_when_explicitly_included():
    current, historical = _chunk(1), _superseded_chunk(2)
    retriever = HybridRetriever(
        semantic=_Retriever([
            DocumentResult(current, 0.91),
            DocumentResult(historical, 0.95),
        ]),
        keyword=None,
        reranker=None,
        vector_top_k=5,
        bm25_top_k=5,
        final_top_k=5,
        include_superseded=True,
    )

    results = retriever.retrieve_with_scores("劳动法处罚标准 历史版本显式开启")

    assert retriever.include_superseded is True
    assert {result.document.chunk_id for result in results} == {
        current.chunk_id,
        historical.chunk_id,
    }


def test_retrieve_keeps_min_results_when_every_score_is_below_threshold():
    first, second, third = _chunk(1), _chunk(2), _chunk(3)
    retriever = HybridRetriever(
        semantic=_Retriever([
            DocumentResult(first, 0.5),
            DocumentResult(second, 0.4),
            DocumentResult(third, 0.3),
        ]),
        keyword=None,
        reranker=_FixedScoreReranker([0.21, 0.11, 0.05]),
        vector_top_k=5,
        bm25_top_k=5,
        final_top_k=3,
        score_threshold=0.3,
        min_results=2,
    )

    documents = retriever.retrieve("劳动法处罚标准 全部低于阈值")

    # 全部低于阈值也不能返回空：调用方无法区分「库里没有」与「分数不够」。
    assert [document.chunk_id for document in documents] == [
        first.chunk_id,
        second.chunk_id,
    ]
    # retrieve_with_scores 不做阈值裁剪，交由上层的 low_quality 标记表达可信度。
    assert len(retriever.retrieve_with_scores("劳动法处罚标准 全部低于阈值")) == 3


def test_retrieve_cuts_tail_when_enough_results_pass_threshold():
    first, second, third = _chunk(1), _chunk(2), _chunk(3)
    retriever = HybridRetriever(
        semantic=_Retriever([
            DocumentResult(first, 0.9),
            DocumentResult(second, 0.8),
            DocumentResult(third, 0.7),
        ]),
        keyword=None,
        reranker=_FixedScoreReranker([0.82, 0.44, 0.09]),
        vector_top_k=5,
        bm25_top_k=5,
        final_top_k=3,
        score_threshold=0.3,
        min_results=1,
    )

    documents = retriever.retrieve("劳动法处罚标准 阈值裁尾")

    assert [document.chunk_id for document in documents] == [
        first.chunk_id,
        second.chunk_id,
    ]
