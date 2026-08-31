"""Agent Tool for bounded similar-case retrieval."""
from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from agent.tools._runtime import (
    bound_results,
    finish_tool_trace,
    raise_tool_error,
    resolve_trace_id,
    serialize_tool_exception,
    start_tool_trace,
)
from agent.tools.schemas import CaseSearchToolInput, RetrievalToolOutput
from services.delilegal.client import DelilegalClient
from services.delilegal.enums import CourtLevel, JudgementType
from services.delilegal.exceptions import DelilegalError
from services.delilegal.processors import compress_case_content
from services.delilegal.schemas import CaseSearchInput


@tool(
    "search_case_tool",
    args_schema=CaseSearchToolInput,
    description=(
        "调用得理案例库检索与当前案情相似的真实裁判文书，并仅返回压缩后的事实、争点、裁判理由和结果。"
        "仅当 Case Analysis Agent 为识别裁判思路、争议焦点或同类案件处理方式确实需要类案时调用；"
        "复杂案情优先传 long_text。不要用于检索法条、司法解释、回答一般法律常识，"
        "也不要在不需要类案或已有充分类案时重复调用。默认且最多返回 Top-K=5。"
    ),
)
async def search_case_tool(
    keywords: list[str] | None = None,
    long_text: str | None = None,
    top_k: int = 5,
    page_no: int = 1,
    sort_field: str = "correlation",
    sort_order: str = "desc",
    case_year_start: str | None = None,
    case_year_end: str | None = None,
    court_levels: list[CourtLevel] | None = None,
    judgement_types: list[JudgementType] | None = None,
    trace_id: Annotated[str | None, InjectedState("trace_id")] = None,
) -> str:
    trace_id = resolve_trace_id(trace_id)
    started = start_tool_trace(trace_id, "search_case_tool", "delilegal_case")
    try:
        request = CaseSearchInput(
            keywords=keywords,
            long_text=long_text,
            page_no=page_no,
            page_size=top_k,
            sort_field=sort_field,
            sort_order=sort_order,
            case_year_start=case_year_start,
            case_year_end=case_year_end,
            court_levels=court_levels,
            judgement_types=judgement_types,
        )
        async with DelilegalClient(trace_id=trace_id) as client:
            response = await client.search_cases(request)
        query = long_text or " ".join(keywords or [])
        values = [
            compress_case_content(item, query, max_section_chars=350)
            for item in response.items[:top_k]
        ]
        results, truncated = bound_results(values, top_k=top_k)
        latency_ms = finish_tool_trace(
            trace_id,
            "search_case_tool",
            "delilegal_case",
            started,
            success=True,
            result_count=len(results),
        )
        return RetrievalToolOutput(
            status="found" if results else "no_relevant_result",
            source_type="delilegal_case",
            trace_id=trace_id,
            latency_ms=latency_ms,
            success=True,
            evidence_insufficient=not results,
            result_count=len(results),
            total_count=response.total_count,
            query_id=response.query_id,
            truncated=truncated or response.total_count > len(results),
            results=results,
        ).model_dump_json(exclude_none=True)
    except DelilegalError as exc:
        latency_ms = finish_tool_trace(
            trace_id,
            "search_case_tool",
            "delilegal_case",
            started,
            success=False,
            error_type=type(exc).__name__,
        )
        raise_tool_error(
            source_type="delilegal_case",
            trace_id=trace_id,
            latency_ms=latency_ms,
            exc=exc,
        )
    except Exception as exc:
        latency_ms = finish_tool_trace(
            trace_id,
            "search_case_tool",
            "delilegal_case",
            started,
            success=False,
            error_type=type(exc).__name__,
        )
        raise_tool_error(
            source_type="delilegal_case",
            trace_id=trace_id,
            latency_ms=latency_ms,
            exc=exc,
        )


search_case_tool.handle_tool_error = serialize_tool_exception
