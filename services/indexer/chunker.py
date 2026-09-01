"""按法律层级切分法规文本，并在无法识别结构时递归回退。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from langchain_text_splitters import RecursiveCharacterTextSplitter

from services.rag.interfaces import LawChunk, compute_content_hash


DEFAULT_MAX_CHUNK_SIZE = 2_000
DEFAULT_CHUNK_OVERLAP = 100

_CN_NUMBER = r"零〇一二三四五六七八九十百千万两\d"
_ARTICLE_PATTERN = re.compile(
    rf"^(第[{_CN_NUMBER}]+条(?:之[{_CN_NUMBER}]+)?)\s*"
)
_HIERARCHY_PATTERN = re.compile(
    rf"^(第[{_CN_NUMBER}]+(?P<level>分编|编|章|节))(?:[\s　]*(?P<title>.*))$"
)
_EXPLICIT_PARAGRAPH_PATTERN = re.compile(rf"^(第[{_CN_NUMBER}]+款)\s*")
_ITEM_LINE_PATTERN = re.compile(
    rf"^(?:[（(]([{_CN_NUMBER}]+)[）)]|([{_CN_NUMBER}]+)、)\s*"
)
_INLINE_ITEM_PATTERN = re.compile(rf"[（(]([{_CN_NUMBER}]+)[）)]")
_AMENDMENT_ITEM_PATTERN = re.compile(
    r"^([一二三四五六七八九十百千万]+)、\s*(.*)"
)

_HEADER_FIELD_MAP = {
    "来源": "source",
    "效力标记": "status",
    "公布日期": "publish_date",
    "施行日期": "effective_date",
    "法规类别": "law_type",
}


@dataclass(frozen=True)
class _Article:
    number: str
    lines: tuple[str, ...]
    hierarchy: tuple[str, ...]


@dataclass(frozen=True)
class _Paragraph:
    number: str
    text: str


class LegalStructuredChunker:
    """优先按编、章、节、条、款、项切分法律文本。"""

    def __init__(
        self,
        *,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        if max_chunk_size <= 0:
            raise ValueError("max_chunk_size 必须大于 0")
        if chunk_overlap < 0 or chunk_overlap >= max_chunk_size:
            raise ValueError("chunk_overlap 必须大于等于 0 且小于 max_chunk_size")
        self.max_chunk_size = max_chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_file(self, file_path: Path) -> list[LawChunk]:
        """读取一个 UTF-8 法律文件并生成结构化检索单元。"""
        text = file_path.read_text(encoding="utf-8")
        law_name = _extract_law_name(file_path)
        lines = text.splitlines()
        base_metadata = _extract_source_metadata(file_path, lines)
        content_lines = _strip_file_header(lines)
        articles, unstructured_lines = self._parse_articles(content_lines)

        if articles:
            chunks: list[LawChunk] = []
            for article in articles:
                chunks.extend(self._chunk_article(law_name, article, base_metadata))
            return chunks

        return self._chunk_unstructured(
            law_name,
            unstructured_lines,
            base_metadata,
        )

    @staticmethod
    def _parse_articles(lines: Sequence[str]) -> tuple[list[_Article], list[str]]:
        articles: list[_Article] = []
        unstructured: list[str] = []
        hierarchy: list[str] = []
        article_no: str | None = None
        article_lines: list[str] = []
        article_hierarchy: tuple[str, ...] = ()

        def flush_article() -> None:
            nonlocal article_no, article_lines, article_hierarchy
            if article_no is not None:
                articles.append(_Article(
                    number=article_no,
                    lines=tuple(line for line in article_lines if line),
                    hierarchy=article_hierarchy,
                ))
            article_no = None
            article_lines = []
            article_hierarchy = ()

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            hierarchy_match = _HIERARCHY_PATTERN.match(line)
            if hierarchy_match:
                flush_article()
                hierarchy = _update_hierarchy(
                    hierarchy,
                    line,
                    hierarchy_match.group("level"),
                )
                continue

            article_match = _ARTICLE_PATTERN.match(line)
            if article_match:
                flush_article()
                article_no = article_match.group(1)
                article_hierarchy = tuple(hierarchy)
                remainder = line[article_match.end():].strip()
                if remainder:
                    article_lines.append(remainder)
                continue

            if article_no is not None:
                article_lines.append(line)
            else:
                unstructured.append(line)

        flush_article()
        return articles, unstructured

    def _chunk_article(
        self,
        law_name: str,
        article: _Article,
        base_metadata: dict[str, str],
    ) -> list[LawChunk]:
        article_text = "\n".join(article.lines).strip()
        whole_content = _with_article_number(article.number, article_text)
        if len(whole_content) <= self.max_chunk_size:
            return [self._make_chunk(
                law_name=law_name,
                hierarchy=article.hierarchy,
                article_no=article.number,
                body=article_text,
                base_metadata=base_metadata,
                strategy="article",
            )]

        paragraphs = _extract_paragraphs(article.lines)
        chunks: list[LawChunk] = []
        for paragraph in paragraphs:
            paragraph_content = _with_article_number(article.number, paragraph.text)
            if len(paragraph_content) <= self.max_chunk_size:
                chunks.append(self._make_chunk(
                    law_name=law_name,
                    hierarchy=article.hierarchy,
                    article_no=article.number,
                    body=paragraph.text,
                    base_metadata=base_metadata,
                    paragraph=paragraph.number,
                    strategy="paragraph",
                ))
                continue

            item_parts = _split_items(paragraph.text)
            if len(item_parts) > 1 or (item_parts and item_parts[0][0]):
                for item_no, item_text in item_parts:
                    if not item_text:
                        continue
                    chunks.extend(self._chunk_structural_leaf(
                        law_name=law_name,
                        hierarchy=article.hierarchy,
                        article_no=article.number,
                        text=item_text,
                        base_metadata=base_metadata,
                        paragraph=paragraph.number,
                        item=item_no,
                    ))
            else:
                chunks.extend(self._chunk_structural_leaf(
                    law_name=law_name,
                    hierarchy=article.hierarchy,
                    article_no=article.number,
                    text=paragraph.text,
                    base_metadata=base_metadata,
                    paragraph=paragraph.number,
                ))
        return chunks

    def _chunk_structural_leaf(
        self,
        *,
        law_name: str,
        hierarchy: Sequence[str],
        article_no: str,
        text: str,
        base_metadata: dict[str, str],
        paragraph: str,
        item: str = "",
    ) -> list[LawChunk]:
        if len(_with_article_number(article_no, text)) <= self.max_chunk_size:
            return [self._make_chunk(
                law_name=law_name,
                hierarchy=hierarchy,
                article_no=article_no,
                body=text,
                base_metadata=base_metadata,
                paragraph=paragraph,
                item=item,
                strategy="item" if item else "paragraph",
            )]

        # 款或项本身仍然过长时，才使用通用递归切分器。
        pieces = self._split_recursively(text, article_no=article_no)
        return [self._make_chunk(
            law_name=law_name,
            hierarchy=hierarchy,
            article_no=article_no,
            body=piece,
            base_metadata=base_metadata,
            paragraph=paragraph,
            item=item,
            fragment=index if len(pieces) > 1 else 0,
            strategy="recursive_fallback",
        ) for index, piece in enumerate(pieces, start=1) if piece.strip()]

    def _chunk_unstructured(
        self,
        law_name: str,
        lines: Sequence[str],
        base_metadata: dict[str, str],
    ) -> list[LawChunk]:
        amendment_chunks = self._chunk_amendment_items(
            law_name,
            lines,
            base_metadata,
        )
        if amendment_chunks is not None:
            return amendment_chunks

        text = "\n".join(lines).strip()
        if not text:
            return []
        pieces = self._split_recursively(text, article_no="前言")
        return [self._make_chunk(
            law_name=law_name,
            hierarchy=(),
            article_no="前言",
            body=piece,
            base_metadata=base_metadata,
            fragment=index if len(pieces) > 1 else 0,
            strategy="recursive_fallback",
        ) for index, piece in enumerate(pieces, start=1) if piece.strip()]

    def _chunk_amendment_items(
        self,
        law_name: str,
        lines: Sequence[str],
        base_metadata: dict[str, str],
    ) -> list[LawChunk] | None:
        preface: list[str] = []
        items: list[tuple[str, list[str]]] = []
        current_no = ""
        current_lines: list[str] = []

        def flush_item() -> None:
            nonlocal current_no, current_lines
            if current_no:
                items.append((current_no, current_lines))
            current_no = ""
            current_lines = []

        for line in lines:
            match = _AMENDMENT_ITEM_PATTERN.match(line)
            if match:
                flush_item()
                current_no = _ordinal(match.group(1), "项")
                if match.group(2).strip():
                    current_lines.append(match.group(2).strip())
            elif current_no:
                current_lines.append(line)
            else:
                preface.append(line)
        flush_item()

        if not items:
            return None

        chunks: list[LawChunk] = []
        preface_text = "\n".join(preface).strip()
        if preface_text:
            chunks.extend(self._chunk_structural_leaf(
                law_name=law_name,
                hierarchy=(),
                article_no="前言",
                text=preface_text,
                base_metadata=base_metadata,
                paragraph="",
            ))
        for item_no, item_lines in items:
            item_text = "\n".join(item_lines).strip()
            chunks.extend(self._chunk_structural_leaf(
                law_name=law_name,
                hierarchy=(),
                article_no=item_no,
                text=item_text,
                base_metadata=base_metadata,
                paragraph="",
                item=item_no,
            ))
        return chunks

    def _split_recursively(self, text: str, *, article_no: str) -> list[str]:
        """为法条号预留空间后执行最终的通用递归切分。"""
        available_size = max(1, self.max_chunk_size - len(article_no) - 1)
        overlap = min(self.chunk_overlap, max(0, available_size - 1))
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=available_size,
            chunk_overlap=overlap,
            separators=["\n\n", "\n", "。", "；", "，", ""],
            keep_separator=True,
        )
        return splitter.split_text(text)

    @staticmethod
    def _make_chunk(
        *,
        law_name: str,
        hierarchy: Sequence[str],
        article_no: str,
        body: str,
        base_metadata: dict[str, str],
        paragraph: str = "",
        item: str = "",
        fragment: int = 0,
        strategy: str,
    ) -> LawChunk:
        content = _with_article_number(article_no, body)
        id_parts = [law_name, article_no]
        if paragraph:
            id_parts.append(paragraph)
        if item:
            id_parts.append(item)
        if fragment:
            id_parts.append(f"片段{fragment}")
        chunk_id = "_".join(id_parts)
        metadata = _build_chunk_metadata(
            law_name=law_name,
            base_metadata=base_metadata,
            hierarchy=hierarchy,
            article_no=article_no,
            paragraph=paragraph,
            item=item,
            content=content,
            strategy=strategy,
        )
        metadata["document_id"] = chunk_id
        return LawChunk(
            law_name=law_name,
            hierarchy=_build_hierarchy_path(hierarchy),
            article_no=article_no,
            content=content,
            chunk_id=chunk_id,
            metadata=metadata,
        )


def _extract_law_name(file_path: Path) -> str:
    stem = file_path.stem
    parts = stem.split("_", 1)
    return parts[1] if len(parts) == 2 else stem


def _strip_file_header(lines: Sequence[str]) -> list[str]:
    """移除 Markdown 标题及其后的键值元信息块。"""
    if not lines:
        return []
    if lines[0].startswith("# "):
        for index, line in enumerate(lines[1:], start=1):
            if not line.strip():
                return list(lines[index + 1:])
        return list(lines[1:])

    content_start = 0
    for index, line in enumerate(lines):
        if line.startswith(("来源:", "抓取时间:")):
            content_start = index + 1
        elif not line.strip() and content_start:
            content_start = index + 1
        else:
            break
    return list(lines[content_start:])


def _extract_source_metadata(file_path: Path, lines: Sequence[str]) -> dict[str, str]:
    metadata = {
        "law_type": "",
        "status": "",
        "publish_date": "",
        "effective_date": "",
        "source": "",
        "source_path": file_path.as_posix(),
    }
    for line in lines[:20]:
        if ":" not in line:
            continue
        label, value = (part.strip() for part in line.split(":", 1))
        target = _HEADER_FIELD_MAP.get(label)
        if target:
            metadata[target] = value
    return metadata


def _update_hierarchy(current: Sequence[str], title: str, level: str) -> list[str]:
    if level == "编":
        return [title]
    if level == "分编":
        return [entry for entry in current if _hierarchy_level(entry) == "编"] + [title]
    if level == "章":
        return [entry for entry in current if _hierarchy_level(entry) in {"编", "分编"}] + [title]
    return [entry for entry in current if _hierarchy_level(entry) in {"编", "分编", "章"}] + [title]


def _hierarchy_level(title: str) -> str:
    match = _HIERARCHY_PATTERN.match(title)
    return match.group("level") if match else ""


def _hierarchy_number(hierarchy: Sequence[str], level: str) -> str:
    for title in reversed(hierarchy):
        match = _HIERARCHY_PATTERN.match(title)
        if match and match.group("level") == level:
            return match.group(1)
    return ""


def _build_hierarchy_path(hierarchy: Sequence[str]) -> str:
    return " > ".join(hierarchy)


def _build_chunk_metadata(
    *,
    law_name: str,
    base_metadata: dict[str, str],
    hierarchy: Sequence[str],
    article_no: str,
    paragraph: str,
    item: str,
    content: str,
    strategy: str,
) -> dict[str, str]:
    return {
        **base_metadata,
        "document_id": "",
        "law_name": law_name,
        "part": _hierarchy_number(hierarchy, "分编") or _hierarchy_number(hierarchy, "编"),
        "chapter": _hierarchy_number(hierarchy, "章"),
        "section": _hierarchy_number(hierarchy, "节"),
        "article": article_no,
        "paragraph": paragraph,
        "item": item,
        "chunking_strategy": strategy,
        "content_hash": compute_content_hash(content),
    }


def _with_article_number(article_no: str, text: str) -> str:
    body = text.strip()
    return f"{article_no} {body}".strip()


def _extract_paragraphs(lines: Sequence[str]) -> list[_Paragraph]:
    paragraphs: list[_Paragraph] = []
    current_lines: list[str] = []
    current_number = ""
    paragraph_index = 0

    def flush_paragraph() -> None:
        nonlocal current_lines, current_number
        if current_lines:
            paragraphs.append(_Paragraph(
                number=current_number,
                text="\n".join(current_lines),
            ))
        current_lines = []
        current_number = ""

    for raw_line in (line for line in lines if line.strip()):
        line = raw_line.strip()
        if _ITEM_LINE_PATTERN.match(line) and current_lines:
            current_lines.append(line)
            continue

        flush_paragraph()
        paragraph_index += 1
        explicit_match = _EXPLICIT_PARAGRAPH_PATTERN.match(line)
        current_number = (
            explicit_match.group(1)
            if explicit_match
            else _ordinal(str(paragraph_index), "款")
        )
        current_lines = [line]

    flush_paragraph()
    if not paragraphs:
        paragraphs.append(_Paragraph(number="第一款", text=""))
    return paragraphs


def _split_items(text: str) -> list[tuple[str, str]]:
    """返回 ``(项号, 原文)``；项前引导语作为无项号片段保留。"""
    matches = list(_INLINE_ITEM_PATTERN.finditer(text))
    if matches:
        parts: list[tuple[str, str]] = []
        prefix = text[:matches[0].start()].strip()
        if prefix:
            parts.append(("", prefix))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            parts.append((_ordinal(match.group(1), "项"), text[match.start():end].strip()))
        return parts

    line_match = _ITEM_LINE_PATTERN.match(text)
    if line_match:
        number = line_match.group(1) or line_match.group(2)
        return [(_ordinal(number, "项"), text)]
    return [("", text)]


def _ordinal(number: str, unit: str) -> str:
    if number.isdigit():
        number = _integer_to_chinese(int(number))
    return f"第{number}{unit}"


def _integer_to_chinese(value: int) -> str:
    if value <= 0:
        return str(value)
    digits = "零一二三四五六七八九"
    if value < 10:
        return digits[value]
    if value < 20:
        return "十" + (digits[value % 10] if value % 10 else "")
    if value < 100:
        return digits[value // 10] + "十" + (digits[value % 10] if value % 10 else "")
    return str(value)


def chunk_law_file(
    file_path: Path,
    *,
    max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[LawChunk]:
    """兼容入口：按法律结构切分单个文件。"""
    return LegalStructuredChunker(
        max_chunk_size=max_chunk_size,
        chunk_overlap=chunk_overlap,
    ).chunk_file(file_path)


def chunk_all_laws(
    laws_dir: str | Path,
    *,
    max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[LawChunk]:
    """扫描目录并按文件名顺序切分全部法律文本。"""
    chunker = LegalStructuredChunker(
        max_chunk_size=max_chunk_size,
        chunk_overlap=chunk_overlap,
    )
    laws_path = Path(laws_dir)
    chunks: list[LawChunk] = []
    for txt_file in sorted(laws_path.glob("*.txt")):
        chunks.extend(chunker.chunk_file(txt_file))
    return chunks
