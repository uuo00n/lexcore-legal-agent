from __future__ import annotations

import json

from services.vectorstore.base import LawChunk


class _FakeRetriever:
    score_threshold = 0.3

    def __init__(self, scored):
        self.scored = scored

    def retrieve(self, query: str, top_k: int = 5):
        return [chunk for chunk, _score in self.scored[:top_k]]

    def retrieve_with_scores(self, query: str, top_k: int = 5):
        return self.scored[:top_k]


def _chunk(article_no: str, content: str) -> LawChunk:
    return LawChunk(
        law_name="民法典",
        hierarchy="合同编",
        article_no=article_no,
        content=content,
        chunk_id=f"民法典_{article_no}",
    )


def test_legal_search_returns_rerank_scores(monkeypatch):
    from mcp_server.tools import search

    monkeypatch.setattr(
        search,
        "get_retriever",
        lambda: _FakeRetriever([
            (_chunk("第六百八十八条", "连带责任保证相关规则。"), 0.72),
            (_chunk("第六百七十三条", "提前收回借款相关规则。"), 0.41),
        ]),
    )

    payload = json.loads(search.legal_search("保证责任 提前收回贷款"))

    assert payload["status"] == "found"
    assert payload["score_threshold"] == 0.3
    assert payload["top_rerank_score"] == 0.72
    assert payload["results"][0]["rerank_score"] == 0.72
    assert payload["results"][1]["rerank_score"] == 0.41


def test_legal_search_marks_low_quality_when_top_score_below_threshold(monkeypatch):
    from mcp_server.tools import search

    monkeypatch.setattr(
        search,
        "get_retriever",
        lambda: _FakeRetriever([
            (_chunk("第六百七十三条", "未按约定用途使用借款。"), 0.29),
        ]),
    )

    payload = json.loads(search.legal_search("新增执行案件 重大不利变化"))

    assert payload["status"] == "low_quality"
    assert payload["top_rerank_score"] == 0.29
    assert payload["score_threshold"] == 0.3
    assert payload["results"][0]["rerank_score"] == 0.29
    assert "web_search_tool" in payload["hint"]
