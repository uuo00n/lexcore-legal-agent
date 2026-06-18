"""合同条款拆分。"""
from __future__ import annotations

import re

from services.contract_agent.schema import Clause


_CHINESE_CLAUSE_RE = re.compile(r"(?m)^\s*(第[一二三四五六七八九十百千万零〇两\d]+条)\s*([^\n]*)")
_DECIMAL_CLAUSE_RE = re.compile(r"(?m)^\s*((?:\d+\.)+\d*|\d+[、.．])\s*([^\n]*)")


def _trim_span(text: str, start: int, end: int) -> tuple[int, int, str]:
    """
    函数作用：
        去除片段首尾空白，同时保留修正后的原文 offset。
    """
    raw = text[start:end]
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw.rstrip())
    trimmed_start = start + leading
    trimmed_end = start + trailing
    return trimmed_start, trimmed_end, text[trimmed_start:trimmed_end]


def _paragraph_fallback(text: str) -> list[Clause]:
    clauses: list[Clause] = []
    for index, match in enumerate(re.finditer(r"\S(?:.*\S)?", text, flags=re.M), start=1):
        start, end, part = _trim_span(text, match.start(), match.end())
        if len(part) < 4:
            continue
        clauses.append(
            Clause(
                id=f"C{len(clauses) + 1}",
                text=part,
                paragraph_index=index,
                start_offset=start,
                end_offset=end,
            )
        )
    return clauses


def segment_clauses(text: str) -> list[Clause]:
    """
    函数作用：
        将合同文本拆成条款片段，并保留原文 offset。
    """
    source = text or ""
    matches = list(_CHINESE_CLAUSE_RE.finditer(source))
    if not matches:
        matches = list(_DECIMAL_CLAUSE_RE.finditer(source))
    if not matches:
        return _paragraph_fallback(source)

    clauses: list[Clause] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        trimmed_start, trimmed_end, part = _trim_span(source, start, end)
        if len(part) < 4:
            continue
        title = (match.group(2) or "").strip() or None
        clauses.append(
            Clause(
                id=f"C{len(clauses) + 1}",
                clause_number=(match.group(1) or "").strip(),
                title=title,
                text=part,
                paragraph_index=len(clauses) + 1,
                start_offset=trimmed_start,
                end_offset=trimmed_end,
            )
        )
    return clauses
