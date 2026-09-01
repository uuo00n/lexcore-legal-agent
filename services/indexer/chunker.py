"""法律文本分块器 —— 将完整法律文本按条款切分为检索单元。

分块策略：
1. 按"第X条"正则匹配切分，每个条款为一个独立 chunk，支持"第X条之一"等增补条款
2. 自动提取编/章/节层级路径作为元数据
3. 对于无条款结构的内容（如宪法序言），按段落切分
"""
from __future__ import annotations

import re
from pathlib import Path

from services.rag.interfaces import LawChunk, compute_content_hash


# 匹配条款号：第一条、第二百三十四条、第二百八十七条之一 等
_ARTICLE_PATTERN = re.compile(
    r"^(第[零一二三四五六七八九十百千万\d]+条(?:之[零一二三四五六七八九十百千万\d]+)?)\s*"
)

# 匹配层级标题：第X编、第X分编、第X章、第X节
_HIERARCHY_PATTERN = re.compile(
    r"^(第[零一二三四五六七八九十百千万\d]+(?:编|分编|章|节))\s+(.+)"
)

# 匹配修正案、修改决定等没有“第X条”结构的分项：一、二、三、
_ITEM_PATTERN = re.compile(r"^([一二三四五六七八九十百千万]+)、\s*(.+)")

_HEADER_FIELD_MAP = {
    "来源": "source",
    "效力标记": "status",
    "公布日期": "publish_date",
    "施行日期": "effective_date",
    "法规类别": "law_type",
}


def _extract_law_name(file_path: Path) -> str:
    """
    函数作用：
        从文件名提取法律简称。
    输入参数：
        - file_path: Path
    输出参数：
        - str
    """
    stem = file_path.stem  # 如 "02_民法典"
    parts = stem.split("_", 1)
    if len(parts) == 2:
        return parts[1]
    return stem


def _build_hierarchy_path(hierarchy_stack: list[str]) -> str:
    """
    函数作用：
        将层级栈拼接为路径字符串。
    输入参数：
        - hierarchy_stack: list[str]
    输出参数：
        - str
    """
    return " > ".join(hierarchy_stack) if hierarchy_stack else ""


def _extract_source_metadata(file_path: Path, lines: list[str]) -> dict[str, str]:
    """从法律文件头部提取可检索的法规元数据。"""
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


def _build_chunk_metadata(
    base_metadata: dict[str, str],
    hierarchy_stack: list[str],
    article_no: str,
    content: str,
    *,
    item: str = "",
) -> dict[str, str]:
    """构造向量库所需的统一法律 chunk metadata。"""
    chapter = next(
        (level for level in reversed(hierarchy_stack) if re.match(r"^第.+章", level)),
        "",
    )
    section = next(
        (level for level in reversed(hierarchy_stack) if re.match(r"^第.+节", level)),
        "",
    )
    return {
        **base_metadata,
        "document_id": "",
        "chapter": chapter,
        "section": section,
        "article": article_no,
        "paragraph": "",
        "item": item,
        "content_hash": compute_content_hash(content),
    }


def chunk_law_file(file_path: Path) -> list[LawChunk]:
    """
    函数作用：
        将单个法律文件按条款切分为 LawChunk 列表。
    输入参数：
        - file_path: Path
    输出参数：
        - list[LawChunk]
    """
    text = file_path.read_text(encoding="utf-8")
    law_name = _extract_law_name(file_path)

    # 跳过文件头部元信息
    lines = text.split("\n")
    base_metadata = _extract_source_metadata(file_path, lines)
    content_start = 0
    if lines and lines[0].startswith("# "):
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "":
                content_start = i + 1
                break
        else:
            content_start = 1
    else:
        for i, line in enumerate(lines):
            if line.startswith("来源:") or line.startswith("抓取时间:"):
                content_start = i + 1
            elif line.strip() == "" and content_start > 0:
                content_start = i + 1
            else:
                break
    lines = lines[content_start:]

    chunks: list[LawChunk] = []
    hierarchy_stack: list[str] = []  # 当前层级栈
    current_article: str | None = None  # 当前条款号
    current_content: list[str] = []  # 当前条款内容累积
    fallback_lines: list[str] = []  # 无条款结构文本的兜底分块输入

    def _flush_current():
        """
        函数作用：
            将当前累积的条款内容保存为 chunk。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        nonlocal current_article, current_content
        if current_article and current_content:
            content_text = "\n".join(current_content).strip()
            if content_text:
                chunk_id = f"{law_name}_{current_article}"
                content = f"{current_article} {content_text}"
                metadata = _build_chunk_metadata(
                    base_metadata,
                    hierarchy_stack,
                    current_article,
                    content,
                )
                metadata["document_id"] = chunk_id
                chunks.append(LawChunk(
                    law_name=law_name,
                    hierarchy=_build_hierarchy_path(hierarchy_stack),
                    article_no=current_article,
                    content=content,
                    chunk_id=chunk_id,
                    metadata=metadata,
                ))
        current_article = None
        current_content = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # 检查是否是层级标题（编/章/节）
        hier_match = _HIERARCHY_PATTERN.match(stripped)
        if hier_match:
            _flush_current()
            level_key = hier_match.group(1)  # 如 "第三编"
            level_title = stripped  # 完整标题

            # 根据层级类型调整栈深度
            if "编" in level_key and "分编" not in level_key:
                hierarchy_stack = [level_title]
            elif "分编" in level_key:
                hierarchy_stack = hierarchy_stack[:1] + [level_title]
            elif "章" in level_key:
                # 保留编/分编层级
                base = [h for h in hierarchy_stack if "编" in h]
                hierarchy_stack = base + [level_title]
            elif "节" in level_key:
                # 保留编/章层级
                base = [h for h in hierarchy_stack if "编" in h or "章" in h]
                hierarchy_stack = base + [level_title]
            continue

        # 检查是否是条款开头
        art_match = _ARTICLE_PATTERN.match(stripped)
        if art_match:
            _flush_current()
            current_article = art_match.group(1)
            # 条款号后面可能紧跟正文
            remainder = stripped[art_match.end():].strip()
            if remainder:
                current_content.append(remainder)
            continue

        # 普通内容行，追加到当前条款
        if current_article:
            current_content.append(stripped)
        else:
            fallback_lines.append(stripped)

    # 处理最后一个条款
    _flush_current()

    if not chunks:
        chunks.extend(_chunk_fallback_paragraphs(
            law_name,
            fallback_lines,
            base_metadata,
        ))

    return chunks


def _chunk_fallback_paragraphs(
    law_name: str,
    lines: list[str],
    base_metadata: dict[str, str],
) -> list[LawChunk]:
    """
    函数作用：
        为修正案、修改决定等非“第X条”结构文本生成可检索分块。
    输入参数：
        - law_name: str
        - lines: list[str]
    输出参数：
        - list[LawChunk]
    """
    chunks: list[LawChunk] = []
    preface: list[str] = []
    current_no: str | None = None
    current_content: list[str] = []

    def _flush_item() -> None:
        nonlocal current_no, current_content
        if current_no and current_content:
            article_no = f"第{current_no}项"
            content_text = "\n".join(current_content).strip()
            content = f"{article_no} {content_text}"
            chunk_id = f"{law_name}_{article_no}"
            metadata = _build_chunk_metadata(
                base_metadata,
                [],
                article_no,
                content,
                item=article_no,
            )
            metadata["document_id"] = chunk_id
            chunks.append(LawChunk(
                law_name=law_name,
                hierarchy="",
                article_no=article_no,
                content=content,
                chunk_id=chunk_id,
                metadata=metadata,
            ))
        current_no = None
        current_content = []

    for line in lines:
        item_match = _ITEM_PATTERN.match(line)
        if item_match:
            _flush_item()
            current_no = item_match.group(1)
            current_content.append(item_match.group(2).strip())
        elif current_no:
            current_content.append(line)
        else:
            preface.append(line)

    _flush_item()

    preface_text = "\n".join(preface).strip()
    if preface_text:
        content = f"前言 {preface_text}"
        chunk_id = f"{law_name}_前言"
        metadata = _build_chunk_metadata(
            base_metadata,
            [],
            "前言",
            content,
        )
        metadata["document_id"] = chunk_id
        chunks.insert(0, LawChunk(
            law_name=law_name,
            hierarchy="",
            article_no="前言",
            content=content,
            chunk_id=chunk_id,
            metadata=metadata,
        ))

    return chunks


def chunk_all_laws(laws_dir: str | Path) -> list[LawChunk]:
    """
    函数作用：
        扫描法律目录，对所有 txt 文件进行分块。
    输入参数：
        - laws_dir: str | Path
    输出参数：
        - list[LawChunk]
    """
    laws_path = Path(laws_dir)
    all_chunks: list[LawChunk] = []

    for txt_file in sorted(laws_path.glob("*.txt")):
        file_chunks = chunk_law_file(txt_file)
        all_chunks.extend(file_chunks)

    return all_chunks
