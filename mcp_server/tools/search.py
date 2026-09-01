"""FastMCP 检索暴露层；所有实现委托给共享 Service Layer。"""
from __future__ import annotations

from typing import Literal

from mcp_server.server import mcp
from services.delilegal.enums import CourtLevel, JudgementType
from services.observability import trace_context
from services.search import (
    CaseSearchParams,
    LawSearchParams,
    LocalLawSearchParams,
    search_case_service,
    search_law_service,
    search_local_law_service,
)


@mcp.tool()
async def search_law(
    query: str,
    top_k: int = 5,
    page_no: int = 1,
    sort_field: Literal["correlation", "time"] = "correlation",
    sort_order: Literal["asc", "desc"] = "desc",
    trace_id: str | None = None,
    thread_id: str | None = None,
    agent_name: str | None = None,
) -> str:
    """检索正式法规、行政法规、地方性法规和司法解释。"""
    with trace_context(
        trace_id=trace_id or "",
        thread_id=thread_id or "",
        node_name="mcp.search_law",
        agent_name=agent_name or "",
        tool_name="search_law",
    ):
        result = await search_law_service(
            LawSearchParams(
                query=query,
                top_k=top_k,
                page_no=page_no,
                sort_field=sort_field,
                sort_order=sort_order,
            ),
            trace_id=trace_id,
        )
    return result.model_dump_json(exclude_none=True)


@mcp.tool()
async def search_case(
    keywords: list[str] | None = None,
    long_text: str | None = None,
    top_k: int = 5,
    page_no: int = 1,
    sort_field: Literal["correlation", "time"] = "correlation",
    sort_order: Literal["asc", "desc"] = "desc",
    case_year_start: str | None = None,
    case_year_end: str | None = None,
    court_levels: list[CourtLevel] | None = None,
    judgement_types: list[JudgementType] | None = None,
    trace_id: str | None = None,
    thread_id: str | None = None,
    agent_name: str | None = None,
) -> str:
    """检索真实裁判类案，并返回压缩后的事实、争点、裁判理由和结果。"""
    with trace_context(
        trace_id=trace_id or "",
        thread_id=thread_id or "",
        node_name="mcp.search_case",
        agent_name=agent_name or "",
        tool_name="search_case",
    ):
        result = await search_case_service(
            CaseSearchParams(
                keywords=keywords,
                long_text=long_text,
                top_k=top_k,
                page_no=page_no,
                sort_field=sort_field,
                sort_order=sort_order,
                case_year_start=case_year_start,
                case_year_end=case_year_end,
                court_levels=court_levels,
                judgement_types=judgement_types,
            ),
            trace_id=trace_id,
        )
    return result.model_dump_json(exclude_none=True)


@mcp.tool()
async def search_local_law(
    query: str,
    top_k: int = 5,
    trace_id: str | None = None,
    thread_id: str | None = None,
    agent_name: str | None = None,
) -> str:
    """检索本地已索引中国法律语料。"""
    with trace_context(
        trace_id=trace_id or "",
        thread_id=thread_id or "",
        node_name="mcp.search_local_law",
        agent_name=agent_name or "",
        tool_name="search_local_law",
    ):
        result = await search_local_law_service(
            LocalLawSearchParams(query=query, top_k=top_k),
            trace_id=trace_id,
        )
    return result.model_dump_json(exclude_none=True)


@mcp.tool()
async def legal_search(
    query: str,
    top_k: int = 5,
    trace_id: str | None = None,
    thread_id: str | None = None,
    agent_name: str | None = None,
) -> str:
    """兼容旧 MCP 客户端的本地法库检索别名。"""
    return await search_local_law(
        query=query,
        top_k=top_k,
        trace_id=trace_id,
        thread_id=thread_id,
        agent_name=agent_name,
    )


__all__ = ["legal_search", "search_case", "search_law", "search_local_law"]
