"""得理法规与类案 LangChain Tools。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from langchain_core.tools import tool

from services.delilegal.client import DelilegalClient
from services.delilegal.enums import CourtLevel, JudgementType
from services.delilegal.exceptions import DelilegalError
from services.delilegal.processors import compress_case_content, extract_relevant_articles
from services.delilegal.schemas import CaseSearchInput, LawSearchInput


def _error_payload(source_type: str, exc: DelilegalError) -> str:
    return json.dumps(
        {
            "status": "error",
            "source_type": source_type,
            "evidence_insufficient": True,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "results": [],
        },
        ensure_ascii=False,
    )


@tool
async def search_law_tool(
    query: str,
    page_no: int = 1,
    page_size: int = 5,
    sort_field: str = "correlation",
    sort_order: str = "desc",
) -> str:
    """用于查询正式法律法规、地方性法规、司法解释等法律规范性内容；不要用于查询裁判案例。"""
    try:
        async with DelilegalClient() as client:
            response = await client.search_laws(
                LawSearchInput(
                    query=query,
                    page_no=page_no,
                    page_size=page_size,
                    sort_field=sort_field,
                    sort_order=sort_order,
                )
            )
    except DelilegalError as exc:
        return _error_payload("delilegal_law", exc)

    retrieved_at = datetime.now(timezone.utc).isoformat()
    results = []
    for item in response.items:
        compact = extract_relevant_articles(item, query)
        compact["source"] = {
            "source_type": item.source_type,
            "source_id": item.id,
            "title": item.title,
            "retrieved_at": retrieved_at,
            "score": None,
        }
        results.append(compact)
    return json.dumps(
        {
            "status": "found" if results else "no_relevant_result",
            "source_type": "delilegal_law",
            "query_id": response.query_id,
            "total_count": response.total_count,
            "evidence_insufficient": not results,
            "results": results,
        },
        ensure_ascii=False,
    )


@tool
async def search_case_tool(
    keywords: list[str] | None = None,
    long_text: str | None = None,
    page_no: int = 1,
    page_size: int = 5,
    sort_field: str = "correlation",
    sort_order: str = "desc",
    case_year_start: str | None = None,
    case_year_end: str | None = None,
    court_levels: list[CourtLevel] | None = None,
    judgement_types: list[JudgementType] | None = None,
) -> str:
    """用于查询与用户案情相似的真实裁判案例；不要用于普通法条检索。复杂案情优先传 long_text。"""
    try:
        request = CaseSearchInput(
            keywords=keywords,
            long_text=long_text,
            page_no=page_no,
            page_size=page_size,
            sort_field=sort_field,
            sort_order=sort_order,
            case_year_start=case_year_start,
            case_year_end=case_year_end,
            court_levels=court_levels,
            judgement_types=judgement_types,
        )
        async with DelilegalClient() as client:
            response = await client.search_cases(request)
    except DelilegalError as exc:
        return _error_payload("delilegal_case", exc)

    query = long_text or " ".join(keywords or [])
    retrieved_at = datetime.now(timezone.utc).isoformat()
    results = []
    for item in response.items:
        compact = compress_case_content(item, query)
        compact["source"] = {
            "source_type": item.source_type,
            "source_id": item.id,
            "title": item.title,
            "retrieved_at": retrieved_at,
            "score": None,
        }
        results.append(compact)
    return json.dumps(
        {
            "status": "found" if results else "no_relevant_result",
            "source_type": "delilegal_case",
            "query_id": response.query_id,
            "total_count": response.total_count,
            "evidence_insufficient": not results,
            "results": results,
        },
        ensure_ascii=False,
    )
