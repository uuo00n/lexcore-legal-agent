from __future__ import annotations

import pytest

from services.rag.interfaces import LawChunk
from services.rag.retriever import HybridRetriever


class _FakeSemantic:
    def __init__(self, chunk: LawChunk):
        self.queries = []
        self.chunk = chunk

    def retrieve(self, query: str, top_k: int = 20):
        self.queries.append(query)
        return [(self.chunk, 0.9)]


class _FakeKeyword:
    def __init__(self, chunk: LawChunk):
        self.queries = []
        self.chunk = chunk

    def retrieve(self, query: str, top_k: int = 20):
        self.queries.append(query)
        return [(self.chunk, 1.0)]


class _FakeReranker:
    def rerank(self, query: str, chunks: list[LawChunk], top_n: int = 5):
        return [(chunk, 0.99) for chunk in chunks[:top_n]]


def test_hybrid_retriever_skips_hyde_for_legal_information_query(monkeypatch):
    chunk = LawChunk(
        law_name="刑法",
        hierarchy="妨害社会管理秩序罪",
        article_no="第三百五十一条",
        content="非法种植罂粟、大麻等毒品原植物的，一律强制铲除。",
        chunk_id="刑法_第三百五十一条",
    )
    semantic = _FakeSemantic(chunk)
    keyword = _FakeKeyword(chunk)

    monkeypatch.setenv("HYDE_ENABLED", "true")
    monkeypatch.setattr(
        "services.retriever.hyde.rewrite_query",
        lambda query: pytest.fail("legal information query should not call rewrite_query"),
    )
    monkeypatch.setattr(
        "services.retriever.hyde.generate_hypothetical_doc",
        lambda query: pytest.fail("legal information query should not call HyDE"),
    )

    retriever = HybridRetriever(
        semantic=semantic,
        keyword=keyword,
        reranker=_FakeReranker(),
        score_threshold=0.1,
    )

    results = retriever.retrieve("种植罂粟几株犯法", top_k=1)

    assert results == [chunk]
    assert semantic.queries == ["种植罂粟几株犯法"]
    assert keyword.queries == ["种植罂粟几株犯法"]


def test_hybrid_retriever_cross_uses_rewrite_and_hyde(monkeypatch):
    chunk = LawChunk(
        law_name="劳动合同法",
        hierarchy="劳动合同的解除和终止",
        article_no="第四十七条",
        content="经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资。",
        chunk_id="劳动合同法_第四十七条",
    )
    semantic = _FakeSemantic(chunk)
    keyword = _FakeKeyword(chunk)
    rewritten_query = "劳动合同期满 公司不续签 经济补偿 第四十六条 第四十七条"
    hyde_doc = "用人单位在劳动合同期满后不续签，劳动者工作三年，可涉及经济补偿金。"

    monkeypatch.setenv("HYDE_ENABLED", "true")
    monkeypatch.setattr("services.retriever.hyde.rewrite_query", lambda query: rewritten_query)
    monkeypatch.setattr("services.retriever.hyde.generate_hypothetical_doc", lambda query: hyde_doc)

    retriever = HybridRetriever(
        semantic=semantic,
        keyword=keyword,
        reranker=_FakeReranker(),
        score_threshold=0.1,
    )

    results = retriever.retrieve("老板让我合同到期就别来了，我干了三年能拿补偿吗", top_k=1)

    assert results == [chunk]
    assert semantic.queries == [
        hyde_doc,
        "老板让我合同到期就别来了，我干了三年能拿补偿吗",
        rewritten_query,
    ]
    assert keyword.queries == [
        "老板让我合同到期就别来了，我干了三年能拿补偿吗",
        rewritten_query,
        hyde_doc,
    ]
