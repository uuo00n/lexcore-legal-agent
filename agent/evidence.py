"""Evidence 规范化模型：统一法条与案例证据的身份、别名与去重口径。

P0-2 / P0-3：所有检索结果在写入 State 之前都必须先转换成这里的 Evidence 模型，
让 Citation 校验、去重、TopK 和 Trace 指标共用同一套标识。
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

LawValidity = Literal["effective", "partial", "invalid", "unknown"]

_BRACKET_RE = re.compile(r"[《》〈〉“”‘’\"'\[\]【】]")
_SPACE_RE = re.compile(r"\s+")
# 仅剥离版本类括注，使「劳动合同法」与「中华人民共和国劳动合同法(2012修正)」归一到同一部法律。
_VERSION_SUFFIX_RE = re.compile(
    r"[（(][^（()）]*?(?:修正|修订|修改|订正|\d{4})[^（()）]*?[)）]"
)
_PUNCTUATION_RE = re.compile(r"[·・.。,，、;；:：!！?？*#\-—_/\\|]")
_STATE_PREFIX = "中华人民共和国"

_ARTICLE_RE = re.compile(
    r"第\s*([零〇一二三四五六七八九十百千万亿两\d]+)\s*条"
    r"(?:\s*之\s*([零〇一二三四五六七八九十百千万亿两\d]+))?"
)
_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1_000, "万": 10_000, "亿": 100_000_000}

_PARTIAL_MARKERS = ("部分失效", "部分废止", "部分修改")
_STILL_VALID_MARKERS = ("尚未失效", "未失效", "现行有效", "有效")
_INVALID_MARKERS = ("已废止", "废止", "已失效", "失效", "expired", "repealed", "abolished", "invalid")
_SCORE_FIELDS = ("rerank_score", "relevance_score", "score", "similarity", "final_score")


def _digits_to_int(text: str) -> int | None:
    """把「八十五」「85」这类条号数字统一成整数，失败时返回 None。"""
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = section = number = 0
    seen = False
    for char in text:
        if char.isdigit():
            number = number * 10 + int(char)
            seen = True
        elif char in _CN_DIGITS:
            number = _CN_DIGITS[char]
            seen = True
        elif char in _CN_UNITS:
            unit = _CN_UNITS[char]
            seen = True
            if unit >= 10_000:
                total = (total + section + number) * unit
                section = number = 0
            else:
                section += (number or 1) * unit
                number = 0
        else:
            return None
    return total + section + number if seen else None


def canonical_law_name(name: Any) -> str:
    """去掉书名号、版本括注、标点与「中华人民共和国」前缀后的法规名。"""
    text = _BRACKET_RE.sub("", str(name or ""))
    text = _VERSION_SUFFIX_RE.sub("", text)
    text = _PUNCTUATION_RE.sub("", text)
    text = _SPACE_RE.sub("", text)
    if text.startswith(_STATE_PREFIX):
        text = text[len(_STATE_PREFIX):]
    return text


def canonical_law_id(name: Any) -> str:
    """同一部法律的稳定标识；别名、简称和修正版本共享同一个 canonical_law_id。"""
    canonical = canonical_law_name(name)
    if not canonical:
        return ""
    return f"law-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def canonical_article_no(value: Any) -> str:
    """条号归一：「第八十五条」「第85条」「第八十五条第二款」都归一到 ``art:85``。"""
    text = _SPACE_RE.sub("", str(value or ""))
    if not text:
        return ""
    match = _ARTICLE_RE.search(text)
    if not match:
        return f"raw:{text}"
    number = _digits_to_int(match.group(1))
    if number is None:
        return f"raw:{text}"
    suffix = _digits_to_int(match.group(2) or "")
    return f"art:{number}" if suffix is None else f"art:{number}-{suffix}"


def law_validity(*values: Any) -> LawValidity:
    """根据时效性字段判断法规有效性；无法判断时返回 unknown。"""
    status = _SPACE_RE.sub("", " ".join(str(value or "") for value in values)).lower()
    if not status:
        return "unknown"
    if any(marker in status for marker in _PARTIAL_MARKERS):
        return "partial"
    if any(marker in status for marker in _STILL_VALID_MARKERS):
        return "effective"
    if any(marker in status for marker in _INVALID_MARKERS):
        return "invalid"
    return "unknown"


def evidence_hash(*parts: Any) -> str:
    payload = "|".join(str(part if part is not None else "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _relevance_score(raw: Mapping[str, Any]) -> float:
    for field in _SCORE_FIELDS:
        value = raw.get(field)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _text(value: Any, *, limit: int = 0) -> str:
    text = str(value if value is not None else "").strip()
    return text[:limit] if limit and len(text) > limit else text


class LawEvidence(BaseModel):
    """规范化后的法条证据；``retrieved_laws`` 中的每一项都是它的 dump。"""

    model_config = ConfigDict(extra="allow")

    evidence_id: str
    ref_id: str = ""
    canonical_law_id: str = ""
    canonical_law_name: str = ""
    display_law_name: str = ""
    # law_name / title 为兼容字段，供既有节点、上下文构建和前端沿用。
    law_name: str = ""
    title: str = ""
    article_no: str = ""
    canonical_article_no: str = ""
    content: str = ""
    source_type: str = ""
    source_id: str = ""
    level_name: str | None = None
    publisher_name: str | None = None
    issued_no: str | None = None
    publish_date: str | None = None
    active_date: str | None = None
    timeliness_name: str | None = None
    validity: LawValidity = "unknown"
    hierarchy: str | None = None
    relevance_score: float = 0.0


class CaseEvidence(BaseModel):
    """规范化后的类案证据；``retrieved_cases`` 中的每一项都是它的 dump。"""

    model_config = ConfigDict(extra="allow")

    evidence_id: str
    ref_id: str = ""
    case_id: str = ""
    case_name: str = ""
    title: str = ""
    case_no: str = ""
    court: str = ""
    cause: str = ""
    case_type: str = ""
    judgment_date: str = ""
    trial_level: str = ""
    summary: str = ""
    dispute_focus: str = ""
    court_reasoning: str = ""
    judgment_result: str = ""
    source_type: str = ""
    source_id: str = ""
    relevance_score: float = 0.0


LAW_CONTENT_MAX_CHARS = 2_000
CASE_SECTION_MAX_CHARS = 600


def normalize_law_evidence(raw: Any) -> LawEvidence | None:
    """清洗并规范化单条法条证据；既无法规名也无正文时返回 None。"""
    if isinstance(raw, LawEvidence):
        return raw
    if not isinstance(raw, Mapping):
        return None
    display_name = _text(raw.get("law_name") or raw.get("title"))
    content = _text(raw.get("content"), limit=LAW_CONTENT_MAX_CHARS)
    if not display_name and not content:
        return None
    article_no = _text(raw.get("article_no"))
    canonical_article = canonical_article_no(article_no)
    canonical_id = canonical_law_id(display_name)
    source_id = _text(raw.get("source_id") or raw.get("id"))
    identity = source_id or canonical_id or canonical_law_name(display_name)
    return LawEvidence(
        evidence_id=f"law-ev-{evidence_hash(identity, canonical_article)}",
        ref_id=_text(raw.get("ref_id")),
        canonical_law_id=canonical_id,
        canonical_law_name=canonical_law_name(display_name),
        display_law_name=display_name,
        law_name=display_name,
        title=_text(raw.get("title") or display_name),
        article_no=article_no,
        canonical_article_no=canonical_article,
        content=content,
        source_type=_text(raw.get("source_type")),
        source_id=source_id,
        level_name=raw.get("level_name"),
        publisher_name=raw.get("publisher_name"),
        issued_no=raw.get("issued_no"),
        publish_date=raw.get("publish_date"),
        active_date=raw.get("active_date"),
        timeliness_name=raw.get("timeliness_name"),
        validity=law_validity(
            raw.get("timeliness_name"),
            raw.get("validity_status"),
            raw.get("effectiveness"),
            raw.get("status"),
        ),
        hierarchy=raw.get("hierarchy"),
        relevance_score=_relevance_score(raw),
    )


def normalize_case_evidence(raw: Any) -> CaseEvidence | None:
    """清洗并规范化单条类案证据；同时修正上游 ``case_number`` 与内部 ``case_no`` 的字段差异。"""
    if isinstance(raw, CaseEvidence):
        return raw
    if not isinstance(raw, Mapping):
        return None
    case_name = _text(raw.get("case_name") or raw.get("title"))
    case_no = _text(raw.get("case_no") or raw.get("case_number"))
    case_id = _text(raw.get("case_id") or raw.get("id"))
    source_id = _text(raw.get("source_id")) or case_id
    if not (case_name or case_no or source_id):
        return None
    summary = _text(
        raw.get("summary") or raw.get("basic_facts") or raw.get("content"),
        limit=CASE_SECTION_MAX_CHARS,
    )
    return CaseEvidence(
        evidence_id=f"case-ev-{evidence_hash(source_id or case_id or case_name, case_no)}",
        ref_id=_text(raw.get("ref_id")),
        case_id=case_id or source_id,
        case_name=case_name,
        title=_text(raw.get("title") or case_name),
        case_no=case_no,
        court=_text(raw.get("court")),
        cause=_text(raw.get("cause")),
        case_type=_text(raw.get("case_type")),
        judgment_date=_text(
            raw.get("judgment_date") or raw.get("judgement_date") or raw.get("case_date")
        ),
        trial_level=_text(raw.get("trial_level") or raw.get("level_of_trial")),
        summary=summary,
        dispute_focus=_text(raw.get("dispute_focus"), limit=CASE_SECTION_MAX_CHARS),
        court_reasoning=_text(raw.get("court_reasoning"), limit=CASE_SECTION_MAX_CHARS),
        judgment_result=_text(raw.get("judgment_result"), limit=CASE_SECTION_MAX_CHARS),
        source_type=_text(raw.get("source_type")) or "delilegal_case",
        source_id=source_id,
        relevance_score=_relevance_score(raw),
    )


def _mapping(item: Any) -> Mapping[str, Any] | None:
    if isinstance(item, BaseModel):
        return item.model_dump()
    return item if isinstance(item, Mapping) else None


def law_evidence_key(item: Any) -> str:
    """P0-3 去重键：优先 ``(source_id, canonical_article_no)``，回退 ``(canonical_law_name, canonical_article_no)``。"""
    data = _mapping(item)
    if data is None:
        return ""
    article = _text(data.get("canonical_article_no")) or canonical_article_no(data.get("article_no"))
    source_id = _text(data.get("source_id") or data.get("id"))
    if source_id:
        return f"law|src:{source_id}|{article}"
    name = _text(data.get("canonical_law_name")) or canonical_law_name(
        data.get("law_name") or data.get("title")
    )
    return f"law|name:{name}|{article}"


def case_evidence_key(item: Any) -> str:
    """类案去重键：优先来源标识，其次案号，最后案件名称。"""
    data = _mapping(item)
    if data is None:
        return ""
    source_id = _text(data.get("source_id") or data.get("case_id") or data.get("id"))
    if source_id:
        return f"case|src:{source_id}"
    case_no = _text(data.get("case_no") or data.get("case_number"))
    if case_no:
        return f"case|no:{_SPACE_RE.sub('', case_no)}"
    return f"case|name:{_text(data.get('case_name') or data.get('title'))}"


def evidence_payload(evidence: BaseModel) -> dict[str, Any]:
    """Evidence 模型写入 State 时的稳定 dict 形态。"""
    return evidence.model_dump(exclude_none=True)


def assign_ref_ids(items: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    """按当前排序为证据分配 ``law_001`` / ``case_001`` 形式的对内引用编号。"""
    for index, item in enumerate(items, start=1):
        item["ref_id"] = f"{prefix}_{index:03d}"
    return items
