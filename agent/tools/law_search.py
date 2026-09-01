"""Agent Tool for official statute and judicial-interpretation retrieval."""
from __future__ import annotations

from typing import Annotated

from langchain_core.tools import ToolException, tool
from langgraph.prebuilt import InjectedState

from agent.tools._runtime import resolve_trace_id, serialize_tool_exception
from agent.tools.schemas import LawSearchToolInput
from services.search import LawSearchParams, search_law_service


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
    """通过共享 Service 检索法规，不经过 MCP。"""
    result = await search_law_service(
        LawSearchParams(
            query=query,
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


search_law_tool.handle_tool_error = serialize_tool_exception
