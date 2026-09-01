"""Local chunking and BM25 retrieval integration tests."""
from __future__ import annotations

from services.indexer.chunker import chunk_law_file
from services.rag.bm25 import BM25Retriever


def _build_fixture_retriever(tmp_path):
    laws = {
        "劳动合同法.txt": "劳动合同法\n第四十六条 用人单位应当向劳动者支付经济补偿。",
        "民法典.txt": "民法典\n第五百七十七条 当事人不履行合同义务的，应当承担违约责任。",
        "刑法.txt": "刑法\n第一条 为了惩罚犯罪，保护人民，根据宪法制定本法。",
        "行政诉讼法.txt": "行政诉讼法\n第一条 为保证人民法院公正及时审理行政案件，制定本法。",
    }
    chunks = []
    for filename, content in laws.items():
        path = tmp_path / filename
        path.write_text(content, encoding="utf-8")
        chunks.extend(chunk_law_file(path))
    return BM25Retriever(chunks)


def test_rag_returns_relevant_law_with_citation_fields(tmp_path):
    retriever = _build_fixture_retriever(tmp_path)

    results = retriever.retrieve("经济补偿 劳动者", top_k=3)

    assert results
    document, score = results[0]
    assert document.law_name == "劳动合同法"
    assert document.article_no == "第四十六条"
    assert "经济补偿" in document.content
    assert score > 0


def test_rag_no_result_returns_empty_list(tmp_path):
    retriever = _build_fixture_retriever(tmp_path)

    assert retriever.retrieve("量子芯片星际航行", top_k=3) == []


def test_uninitialized_rag_retriever_returns_no_results():
    assert BM25Retriever().retrieve("劳动合同", top_k=5) == []
