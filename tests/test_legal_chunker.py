"""法律结构化切分器测试。"""
from __future__ import annotations

from pathlib import Path

from services.indexer.chunker import chunk_law_file


def _write_law(tmp_path: Path, body: str, name: str = "中华人民共和国测试法") -> Path:
    path = tmp_path / f"01_{name}.txt"
    path.write_text(body, encoding="utf-8")
    return path


def test_chunk_with_chapter_and_article_keeps_parent_metadata(tmp_path):
    law_path = _write_law(
        tmp_path,
        "第一编 总则\n第一章 基本规定\n第一节 一般规定\n第一条 为了规范测试活动，制定本法。",
    )

    chunks = chunk_law_file(law_path)

    assert len(chunks) == 1
    assert chunks[0].content == "第一条 为了规范测试活动，制定本法。"
    assert chunks[0].metadata["law_name"] == "中华人民共和国测试法"
    assert chunks[0].metadata["part"] == "第一编"
    assert chunks[0].metadata["chapter"] == "第一章"
    assert chunks[0].metadata["section"] == "第一节"
    assert chunks[0].metadata["article"] == "第一条"


def test_chunk_with_article_only(tmp_path):
    law_path = _write_law(
        tmp_path,
        "第一条 本法适用于测试活动。\n第二条 测试活动应当遵循诚信原则。",
    )

    chunks = chunk_law_file(law_path)

    assert [chunk.article_no for chunk in chunks] == ["第一条", "第二条"]
    assert all(chunk.metadata["chapter"] == "" for chunk in chunks)
    assert all(chunk.metadata["paragraph"] == "" for chunk in chunks)


def test_article_across_paragraphs_stays_complete(tmp_path):
    law_path = _write_law(
        tmp_path,
        "第一章 总则\n第一条 第一款内容。\n第二款内容。\n第三款内容。\n第二条 下一条内容。",
    )

    chunks = chunk_law_file(law_path)

    assert len(chunks) == 2
    assert chunks[0].content == "第一条 第一款内容。\n第二款内容。\n第三款内容。"
    assert chunks[0].metadata["paragraph"] == ""


def test_long_article_splits_by_paragraph_then_item(tmp_path):
    long_item = "甲" * 45
    law_path = _write_law(
        tmp_path,
        "\n".join([
            "第四章 法律责任",
            "第四十七条 第一款简短内容。",
            "第二款引导语：",
            f"（一）{long_item}；",
            f"（二）{long_item}。",
        ]),
        name="中华人民共和国劳动合同法",
    )

    chunks = chunk_law_file(law_path, max_chunk_size=80, chunk_overlap=10)

    assert len(chunks) >= 3
    assert chunks[0].metadata["paragraph"] == "第一款"
    assert {chunk.metadata["item"] for chunk in chunks} >= {"第一项", "第二项"}
    assert all(chunk.metadata["law_name"] == "中华人民共和国劳动合同法" for chunk in chunks)
    assert all(chunk.metadata["chapter"] == "第四章" for chunk in chunks)
    assert all(chunk.metadata["article"] == "第四十七条" for chunk in chunks)
    assert all(chunk.chunk_id != "中华人民共和国劳动合同法_第四十七条" for chunk in chunks)


def test_unstructured_text_uses_recursive_fallback(tmp_path):
    law_path = _write_law(
        tmp_path,
        "这是没有法律层级标记的普通文本。" * 20,
        name="普通材料",
    )

    chunks = chunk_law_file(law_path, max_chunk_size=60, chunk_overlap=10)

    assert len(chunks) > 1
    assert all(chunk.metadata["chunking_strategy"] == "recursive_fallback" for chunk in chunks)
    assert all(chunk.metadata["chapter"] == "" for chunk in chunks)
    assert all(chunk.metadata["article"] == "前言" for chunk in chunks)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
