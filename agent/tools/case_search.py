"""Agent Tool for bounded similar-case retrieval."""
from __future__ import annotations

from typing import Annotated

from langchain_core.tools import ToolException, tool
from langgraph.prebuilt import InjectedState

from agent.tools._runtime import resolve_trace_id, serialize_tool_exception
from agent.tools.schemas import CaseSearchToolInput
from services.search import CaseSearchParams, search_case_service


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
    trace_id: Annotated[str | None, InjectedState("trace_id")] = None,
) -> str:
    """通过共享 Service 检索类案，不经过 MCP。"""
    result = await search_case_service(
        CaseSearchParams(
            keywords=keywords,
            long_text=long_text,
            top_k=top_k,
            page_no=page_no,
            sort_field=sort_field,
            sort_order=sort_order,
        ),
        trace_id=resolve_trace_id(trace_id),
    )
    payload = result.model_dump_json(exclude_none=True)
    if not result.success:
        raise ToolException(payload)
    return payload


search_case_tool.handle_tool_error = serialize_tool_exception
