"""Agent Tool wrapper for the indexed local statute corpus."""
from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.tools import ToolException, tool
from langgraph.prebuilt import InjectedState

from agent.tools._runtime import (
    bound_results,
    finish_tool_trace,
    raise_tool_error,
    resolve_trace_id,
    serialize_tool_exception,
    start_tool_trace,
)
from agent.tools.schemas import RagSearchToolInput, RetrievalToolOutput
from services.mcp_client import call_tool
from services.observability import get_trace_context


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
    trace_id = resolve_trace_id(trace_id)
    started = start_tool_trace(trace_id, "retrieve_local_law_tool", "local_rag")
    if len(query) < 2:
        latency_ms = finish_tool_trace(
            trace_id,
            "retrieve_local_law_tool",
            "local_rag",
            started,
            success=False,
            error_type="invalid_input",
        )
        raise ToolException(
            RetrievalToolOutput(
                status="error",
                source_type="local_rag",
                trace_id=trace_id,
                latency_ms=latency_ms,
                success=False,
                evidence_insufficient=True,
                error={
                    "code": "invalid_input",
                    "message": "检索词至少需要两个非空白字符，请提炼具体法律问题后重试。",
                    "retryable": True,
                },
            ).model_dump_json(exclude_none=True)
        )
    try:
        trace_context = get_trace_context()
        raw = await call_tool(
            "legal_search",
            {
                "query": query,
                "top_k": top_k,
                "trace_id": trace_id,
                "thread_id": trace_context.thread_id,
                "agent_name": trace_context.agent_name,
            },
        )
        payload: dict[str, Any] = json.loads(raw) if isinstance(raw, str) else dict(raw)
        raw_results = payload.get("results") if isinstance(payload.get("results"), list) else []
        results, truncated = bound_results(raw_results, top_k=top_k)
        status = payload.get("status")
        if status not in {"found", "no_relevant_result", "low_quality"}:
            status = "found" if results else "no_relevant_result"
        latency_ms = finish_tool_trace(
            trace_id,
            "retrieve_local_law_tool",
            "local_rag",
            started,
            success=True,
            result_count=len(results),
        )
        return RetrievalToolOutput(
            status=status,
            source_type="local_rag",
            trace_id=trace_id,
            latency_ms=latency_ms,
            success=True,
            evidence_insufficient=bool(payload.get("evidence_insufficient", not results)),
            result_count=len(results),
            truncated=truncated,
            results=results,
            hint=str(payload.get("hint") or "") or None,
            score_threshold=payload.get("score_threshold"),
            top_rerank_score=payload.get("top_rerank_score"),
        ).model_dump_json(exclude_none=True)
    except Exception as exc:
        latency_ms = finish_tool_trace(
            trace_id,
            "retrieve_local_law_tool",
            "local_rag",
            started,
            success=False,
            error_type=type(exc).__name__,
        )
        raise_tool_error(
            source_type="local_rag",
            trace_id=trace_id,
            latency_ms=latency_ms,
            exc=exc,
        )


retrieve_local_law_tool.handle_tool_error = serialize_tool_exception
