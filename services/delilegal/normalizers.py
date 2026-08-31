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


def normalize_law_response(payload: Any) -> LawSearchResponse:
    body = _body(payload)
    items = [
        LawSearchResult(
            id=str(item.get("id") or ""),
            title=str(item.get("title") or ""),
            issued_no=item.get("issuedNo"),
            publisher_name=item.get("publisherName"),
            publish_date=item.get("publishDate"),
            active_date=item.get("activeDate"),
            timeliness_name=item.get("timelinessName"),
            level_name=item.get("levelName"),
            content=str(item.get("content") or ""),
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
            case_type=item.get("caseType"),
            cause=item.get("cause"),
            judgement_type=item.get("judgementType"),
            judgement_date=item.get("judgementDate"),
            court=item.get("court"),
            case_number=item.get("caseNumber"),
            level_of_trial=item.get("levelOfTrial"),
            publish_type=item.get("publishType"),
            publish_type_name=item.get("publishTypeName"),
            content=str(item.get("content") or ""),
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
