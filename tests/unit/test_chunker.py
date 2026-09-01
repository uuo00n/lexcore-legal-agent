"""Legal document chunking tests."""
from __future__ import annotations

import pytest

from services.indexer.chunker import LegalStructuredChunker, chunk_law_file


def test_chunker_preserves_law_article_and_hierarchy(tmp_path):
    source = tmp_path / "劳动合同法.txt"
    source.write_text(
        "中华人民共和国劳动合同法\n"
        "第二章 劳动合同的订立\n"
        "第十条 建立劳动关系，应当订立书面劳动合同。\n"
        "第十一条 未同时订立书面劳动合同的，劳动报酬按照集体合同规定执行。\n",
        encoding="utf-8",
    )

    chunks = chunk_law_file(source, max_chunk_size=120)

    assert [chunk.article_no for chunk in chunks] == ["第十条", "第十一条"]
    assert all(chunk.law_name == "劳动合同法" for chunk in chunks)
    assert all("第二章" in chunk.hierarchy for chunk in chunks)
    assert chunks[0].content.startswith("第十条")


def test_long_article_is_split_without_losing_article_number(tmp_path):
    source = tmp_path / "测试法.txt"
    source.write_text(
        "测试法\n第一条 " + "劳动者依法享有休息休假的权利。" * 20,
        encoding="utf-8",
    )

    chunks = chunk_law_file(source, max_chunk_size=80, chunk_overlap=10)

    assert len(chunks) > 1
    assert all(chunk.article_no == "第一条" for chunk in chunks)
    assert all(chunk.content.startswith("第一条") for chunk in chunks)


@pytest.mark.parametrize(
    ("max_size", "overlap"),
    [(0, 0), (100, -1), (100, 100)],
)
def test_chunker_rejects_invalid_size_configuration(max_size, overlap):
    with pytest.raises(ValueError):
        LegalStructuredChunker(max_chunk_size=max_size, chunk_overlap=overlap)
