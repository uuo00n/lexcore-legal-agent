"""Agent Tool for official statute and judicial-interpretation retrieval."""
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
from agent.tools.schemas import LawSearchToolInput, RetrievalToolOutput
from services.delilegal.client import DelilegalClient
from services.delilegal.exceptions import DelilegalError
from services.delilegal.processors import extract_relevant_articles
from services.delilegal.schemas import LawSearchInput


@tool(
    "search_law_tool",
    args_schema=LawSearchToolInput,
    description=(
        "调用得理官方法律库检索现行法律法规、行政法规、地方性法规和司法解释。"
        "当回答需要核实具体法条、效力状态或正式规范依据，且现有可信法规证据不足时调用；"
        "Statute Retrieval Agent 应优先使用本工具。不要用于查找裁判案例、分析用户事实、"
        "生成法律结论，也不要在已有充分法规结果时用近似 query 重复调用。默认且最多返回 Top-K=5。"
    ),
)
async def search_law_tool(
    query: str,
    top_k: int = 5,
    page_no: int = 1,
    sort_field: str = "correlation",
    sort_order: str = "desc",
    trace_id: Annotated[str | None, InjectedState("trace_id")] = None,
) -> str:
    trace_id = resolve_trace_id(trace_id)
    started = start_tool_trace(trace_id, "search_law_tool", "delilegal_law")
    try:
        async with DelilegalClient(trace_id=trace_id) as client:
            response = await client.search_laws(
                LawSearchInput(
                    query=query,
                    page_no=page_no,
                    page_size=top_k,
                    sort_field=sort_field,
                    sort_order=sort_order,
                )
            )
        values = [
            extract_relevant_articles(item, query, max_articles=3, max_chars=1_600)
            for item in response.items[:top_k]
        ]
        results, truncated = bound_results(values, top_k=top_k)
        latency_ms = finish_tool_trace(
            trace_id,
            "search_law_tool",
            "delilegal_law",
            started,
            success=True,
            result_count=len(results),
        )
        return RetrievalToolOutput(
            status="found" if results else "no_relevant_result",
            source_type="delilegal_law",
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
            "search_law_tool",
            "delilegal_law",
            started,
            success=False,
            error_type=type(exc).__name__,
        )
        raise_tool_error(
            source_type="delilegal_law",
            trace_id=trace_id,
            latency_ms=latency_ms,
            exc=exc,
        )
    except Exception as exc:
        latency_ms = finish_tool_trace(
            trace_id,
            "search_law_tool",
            "delilegal_law",
            started,
            success=False,
            error_type=type(exc).__name__,
        )
        raise_tool_error(
            source_type="delilegal_law",
            trace_id=trace_id,
            latency_ms=latency_ms,
            exc=exc,
        )


search_law_tool.handle_tool_error = serialize_tool_exception
