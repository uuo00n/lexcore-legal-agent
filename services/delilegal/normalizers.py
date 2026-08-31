"""将得理原始 JSON 转换为稳定的内部模型。"""
from __future__ import annotations

from typing import Any

from services.delilegal.exceptions import DelilegalInvalidResponseError
from services.delilegal.schemas import (
    CaseSearchInput,
    CaseSearchResponse,
    CaseSearchResult,
    LawSearchInput,
    LawSearchResponse,
    LawSearchResult,
)


def build_case_request(value: CaseSearchInput) -> dict[str, Any]:
    condition: dict[str, Any] = {}
    optional = {
        "caseYearStart": value.case_year_start,
        "caseYearEnd": value.case_year_end,
        "courtLevelArr": [item.value for item in value.court_levels] if value.court_levels else None,
        "judgementTypeArr": [item.value for item in value.judgement_types] if value.judgement_types else None,
        "longText": value.long_text,
        "keywordArr": value.keywords if not value.long_text else None,
    }
    condition.update({key: item for key, item in optional.items() if item is not None})
    return {
        "pageNo": value.page_no,
        "pageSize": value.page_size,
        "sortField": value.sort_field,
        "sortOrder": value.sort_order,
        "condition": condition,
    }


def build_law_request(value: LawSearchInput) -> dict[str, Any]:
    """构造法规关键词检索请求。"""
    return {
        "pageNo": value.page_no,
        "pageSize": value.page_size,
        "sortField": value.sort_field,
        "sortOrder": value.sort_order,
        "condition": {"keywordArr": [value.query]},
    }


def _body(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DelilegalInvalidResponseError("Delilegal response must be a JSON object.")
    current = payload
    while True:
        nested = next(
            (current.get(key) for key in ("data", "result", "page") if isinstance(current.get(key), dict)),
            None,
        )
        if nested is None or nested is current:
            break
        current = nested
    return current


def _items(body: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "list", "records", "rows", "dataList"):
        value = body.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _integer(body: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = body.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                break
    return 0


def _score(item: dict[str, Any]) -> float | None:
    for key in ("score", "correlation", "similarity"):
        value = item.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def normalize_law_response(payload: Any) -> LawSearchResponse:
    body = _body(payload)
    items = [
        LawSearchResult(
            id=str(item.get("id") or ""),
            title=str(item.get("title") or ""),
            law_name=str(item.get("lawName") or item.get("title") or ""),
            article=item.get("article") or item.get("articleNo"),
            content=str(item.get("content") or ""),
            publish_date=item.get("publishDate"),
            effective_date=item.get("effectiveDate") or item.get("activeDate"),
            status=item.get("status") or item.get("timelinessName"),
            source=str(item.get("source") or "delilegal"),
            score=_score(item),
            issued_no=item.get("issuedNo"),
            publisher_name=item.get("publisherName"),
            active_date=item.get("activeDate") or item.get("effectiveDate"),
            timeliness_name=item.get("timelinessName") or item.get("status"),
            level_name=item.get("levelName"),
            highlights=item.get("highlights"),
        )
        for item in _items(body)
        if item.get("id") is not None and item.get("title") is not None
    ]
    return LawSearchResponse(
        query_id=body.get("queryId"),
        total_count=_integer(body, "totalCount", "total", "count"),
        total_page=_integer(body, "totalPage", "pages"),
        items=items,
    )


def normalize_case_response(payload: Any) -> CaseSearchResponse:
    body = _body(payload)
    items = [
        CaseSearchResult(
            id=str(item.get("id") or ""),
            title=str(item.get("title") or ""),
            court=item.get("court"),
            case_number=item.get("caseNumber"),
            case_date=item.get("caseDate") or item.get("judgementDate"),
            cause=item.get("cause"),
            summary=item.get("summary"),
            judgment=str(item.get("judgment") or item.get("content") or ""),
            source=str(item.get("source") or "delilegal"),
            score=_score(item),
            case_type=item.get("caseType"),
            judgement_type=item.get("judgementType"),
            judgement_date=item.get("judgementDate") or item.get("caseDate"),
            level_of_trial=item.get("levelOfTrial"),
            publish_type=item.get("publishType"),
            publish_type_name=item.get("publishTypeName"),
            content=str(item.get("content") or item.get("judgment") or ""),
        )
        for item in _items(body)
        if item.get("id") is not None and item.get("title") is not None
    ]
    return CaseSearchResponse(
        query_id=body.get("queryId"),
        total_count=_integer(body, "totalCount", "total", "count"),
        total_page=_integer(body, "totalPage", "pages"),
        items=items,
    )
