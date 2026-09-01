"""Agent Tool wrapper for the indexed local statute corpus."""
from __future__ import annotations

from typing import Annotated

from langchain_core.tools import ToolException, tool
from langgraph.prebuilt import InjectedState

from agent.tools._runtime import resolve_trace_id, serialize_tool_exception
from agent.tools.schemas import RagSearchToolInput
from services.search import LocalLawSearchParams, search_local_law_service


@tool(
    "retrieve_local_law_tool",
    args_schema=RagSearchToolInput,
    description=(
        "只检索项目已建立索引的本地中国法律 DOC 语料，不访问外部网络。"
        "当需要补充本地法库依据或得理服务不可用时调用；不要用于类案检索、事实分析或重复检索。"
        "默认且最多返回 Top-K=5，低质量命中必须视为证据不足。"
    ),
)
async def retrieve_local_law_tool(
    query: str,
    top_k: int = 5,
    trace_id: Annotated[str | None, InjectedState("trace_id")] = None,
) -> str:
    """直接调用进程内 RAG Service，不经过 MCP Client。"""
    result = await search_local_law_service(
        LocalLawSearchParams(query=query, top_k=top_k),
        trace_id=resolve_trace_id(trace_id),
    )
    payload = result.model_dump_json(exclude_none=True)
    if not result.success:
        raise ToolException(payload)
    return payload


retrieve_local_law_tool.handle_tool_error = serialize_tool_exception
