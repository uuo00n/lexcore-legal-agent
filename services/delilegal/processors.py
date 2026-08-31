"""压缩法规全文和裁判文书，防止整篇内容进入模型上下文。"""
from __future__ import annotations

import re
from typing import Any

from services.delilegal.schemas import CaseSearchResult, LawSearchResult

_ARTICLE_START = re.compile(r"(?=第[一二三四五六七八九十百千万零〇两\d]+条(?:之[一二三四五六七八九十百千万零〇两\d]+)?)")
_ARTICLE_NO = re.compile(r"^(第[一二三四五六七八九十百千万零〇两\d]+条(?:之[一二三四五六七八九十百千万零〇两\d]+)?)")
_SECTION_NAMES = {
    "basic_facts": ("基本事实", "经审理查明", "本院查明", "事实与理由"),
    "dispute_focus": ("争议焦点", "本案焦点"),
    "court_reasoning": ("本院认为", "法院认为"),
    "judgment_result": ("判决如下", "裁定如下", "判决结果", "裁判结果"),
    "legal_references": ("法律依据", "依照", "依据"),
    "evidence_summary": ("证据", "质证"),
}


def _terms(query: str) -> set[str]:
    chunks = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9]{2,}", query)
    terms: set[str] = set(chunks)
    for chunk in chunks:
        terms.update(chunk[index:index + 2] for index in range(max(0, len(chunk) - 1)))
    return terms


def _score(text: str, terms: set[str]) -> int:
    return sum(text.count(term) for term in terms)


def extract_relevant_articles(
    law: LawSearchResult, query: str, *, max_articles: int = 3, max_chars: int = 2400
) -> dict[str, Any]:
    parts = [part.strip() for part in _ARTICLE_START.split(law.content) if part.strip()]
    articles = [part for part in parts if _ARTICLE_NO.match(part)]
    if not articles and law.highlights:
        articles = [str(item) for item in law.highlights if item]
    terms = _terms(query)
    ranked = sorted(enumerate(articles), key=lambda pair: (-_score(pair[1], terms), pair[0]))
    selected = [text for _index, text in ranked[:max_articles]]
    if not selected and law.content:
        selected = [law.content[:max_chars]]
    compact: list[dict[str, str]] = []
    used = 0
    for text in selected:
        remaining = max_chars - used
        if remaining <= 0:
            break
        excerpt = text[:remaining]
        match = _ARTICLE_NO.match(excerpt)
        compact.append({"article_no": match.group(1) if match else "相关内容", "content": excerpt})
        used += len(excerpt)
    return {
        "id": law.id,
        "title": law.title,
        "law_name": law.law_name,
        "article": law.article,
        "issued_no": law.issued_no,
        "publisher_name": law.publisher_name,
        "publish_date": law.publish_date,
        "effective_date": law.effective_date,
        "status": law.status,
        "active_date": law.active_date,
        "timeliness_name": law.timeliness_name,
        "level_name": law.level_name,
        "source_type": law.source_type,
        "score": law.score,
        "relevant_articles": compact,
    }


def _section(content: str, markers: tuple[str, ...], max_chars: int) -> str | None:
    starts = [(content.find(marker), marker) for marker in markers if content.find(marker) >= 0]
    if not starts:
        return None
    start, marker = min(starts)
    tail = content[start + len(marker):]
    next_heading = re.search(
        r"\n(?:本院认为|法院认为|判决如下|裁定如下|争议焦点|经审理查明|法律依据)[：:]?",
        tail,
    )
    end = next_heading.start() if next_heading else len(tail)
    return tail[:end].strip(" ：:\n")[:max_chars] or None


def compress_case_content(
    case: CaseSearchResult, query: str, *, max_section_chars: int = 900
) -> dict[str, Any]:
    sections = {
        name: _section(case.content, markers, max_section_chars)
        for name, markers in _SECTION_NAMES.items()
    }
    if not any(sections.values()) and case.content:
        paragraphs = [part.strip() for part in re.split(r"\n+", case.content) if part.strip()]
        terms = _terms(query)
        ranked = sorted(enumerate(paragraphs), key=lambda pair: (-_score(pair[1], terms), pair[0]))
        sections["basic_facts"] = "\n".join(text for _i, text in ranked[:3])[:max_section_chars]
    return {
        "id": case.id,
        "title": case.title,
        "case_type": case.case_type,
        "cause": case.cause,
        "judgement_type": case.judgement_type,
        "judgement_date": case.judgement_date,
        "case_date": case.case_date,
        "court": case.court,
        "case_number": case.case_number,
        "level_of_trial": case.level_of_trial,
        "publish_type": case.publish_type,
        "publish_type_name": case.publish_type_name,
        "source_type": case.source_type,
        "score": case.score,
        **sections,
    }
