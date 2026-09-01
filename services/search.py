"""法律检索 Service Layer，供 LangChain Tool 与 FastMCP Tool 共同调用。"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from services.delilegal.client import DelilegalClient
from services.delilegal.enums import SourceType
from services.delilegal.exceptions import (
    DelilegalAuthenticationError,
    DelilegalConfigurationError,
    DelilegalError,
    DelilegalInvalidResponseError,
    DelilegalTimeoutError,
)
from services.delilegal.processors import compress_case_content, extract_relevant_articles
from services.delilegal.schemas import CaseSearchInput, LawSearchInput
from services.local_legal_retriever import LocalLegalRetriever
from services.rag.retriever import get_retriever
from services.errors import RetrievalError
from services.retry import is_retryable_exception


log = logging.getLogger("legal.search_service")
MAX_SEARCH_TOP_K = 5
MAX_SEARCH_OUTPUT_CHARS = 12_000
_LOCAL_RAG_LOCK = threading.Lock()


class LawSearchParams(BaseModel):
    """法规检索的共享业务参数。"""

    query: str = Field(min_length=2, max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=MAX_SEARCH_TOP_K)
    page_no: int = Field(default=1, ge=1, le=100)
    sort_field: Literal["correlation", "time", "activeDate"] = "correlation"
    sort_order: Literal["asc", "desc"] = "desc"

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("query must contain at least two non-whitespace characters")
        return value


class CaseSearchParams(BaseModel):
    """类案检索的共享业务参数。"""

    keywords: list[str] | None = Field(default=None, max_length=8)
    long_text: str | None = Field(default=None, max_length=6_000)
    top_k: int = Field(default=5, ge=1, le=MAX_SEARCH_TOP_K)
    page_no: int = Field(default=1, ge=1, le=100)
    sort_field: Literal["correlation", "time"] = "correlation"
    sort_order: Literal["asc", "desc"] = "desc"

    @model_validator(mode="after")
    def validate_search_mode(self) -> "CaseSearchParams":
        if self.long_text and self.long_text.strip():
            self.long_text = self.long_text.strip()
            self.keywords = None
        elif self.keywords:
            cleaned = [item.strip()[:100] for item in self.keywords if item.strip()]
            self.keywords = cleaned or None
        if not self.long_text and not self.keywords:
            raise ValueError("long_text or keywords is required")
        return self


class LocalLawSearchParams(BaseModel):
    """本地法库检索的共享业务参数。"""

    query: str = Field(max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=MAX_SEARCH_TOP_K)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        return value.strip()


class ToolErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False


class SearchServiceResult(BaseModel):
    """检索 Service 的稳定输出；不会暴露上游原始响应。"""

    status: Literal["found", "no_relevant_result", "low_quality", "error"]
    source_type: Literal["local_rag", "delilegal_law", "delilegal_case"]
    trace_id: str
    latency_ms: float = Field(ge=0)
    success: bool
    evidence_insufficient: bool
    result_count: int = Field(default=0, ge=0, le=MAX_SEARCH_TOP_K)
    total_count: int | None = Field(default=None, ge=0)
    query_id: str | None = None
    truncated: bool = False
    results: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_SEARCH_TOP_K)
    error: ToolErrorDetail | None = None
    hint: str | None = None
    score_threshold: float | None = None
    top_rerank_score: float | None = None


def _resolve_trace_id(value: str | None) -> str:
    return value or f"service-{uuid.uuid4().hex[:16]}"


def _record_tool_event(
    trace_id: str,
    event_type: str,
    tool_name: str,
    payload: dict[str, Any],
) -> None:
    try:
        from services.observability import record_event

        record_event(trace_id, event_type, name=tool_name, payload=payload)
    except Exception as exc:
        log.debug("search service trace skipped: %s", type(exc).__name__)


def _start(trace_id: str, tool_name: str, source_type: str) -> float:
    _record_tool_event(trace_id, "tool_start", tool_name, {"source_type": source_type})
    return time.perf_counter()


def _finish(
    trace_id: str,
    tool_name: str,
    source_type: str,
    started: float,
    *,
    success: bool,
    result_count: int = 0,
    error_type: str = "",
) -> float:
    latency_ms = round((time.perf_counter() - started) * 1_000, 2)
    _record_tool_event(
        trace_id,
        "tool_end",
        tool_name,
        {
            "source_type": source_type,
            "latency_ms": latency_ms,
            "success": success,
            "result_count": result_count,
            "error_type": error_type,
        },
    )
    log.info(
        "search_service trace_id=%s tool=%s source_type=%s latency_ms=%s success=%s result_count=%s error_type=%s",
        trace_id,
        tool_name,
        source_type,
        latency_ms,
        success,
        result_count,
        error_type or "-",
    )
    return latency_ms


def _bound_results(
    values: list[dict[str, Any]],
    *,
    top_k: int,
    max_chars: int = MAX_SEARCH_OUTPUT_CHARS,
) -> tuple[list[dict[str, Any]], bool]:
    selected: list[dict[str, Any]] = []
    used = 0
    for value in values[:top_k]:
        size = len(json.dumps(value, ensure_ascii=False, default=str))
        if selected and used + size > max_chars:
            break
        selected.append(value)
        used += size
    return selected, len(selected) < len(values)


def _error_detail(exc: Exception) -> ToolErrorDetail:
    if isinstance(exc, DelilegalAuthenticationError):
        return ToolErrorDetail(
            code="authentication_failed",
            message="得理服务认证失败，请检查服务端配置后重试。",
            retryable=False,
        )
    if isinstance(exc, DelilegalConfigurationError):
        return ToolErrorDetail(
            code="configuration_error",
            message="得理服务尚未正确配置，当前不能使用该检索源。",
            retryable=False,
        )
    if isinstance(exc, DelilegalTimeoutError):
        return ToolErrorDetail(
            code="upstream_timeout",
            message="得理服务响应超时，可稍后重试一次；不要据此编造检索结果。",
            retryable=True,
        )
    if isinstance(exc, DelilegalInvalidResponseError):
        return ToolErrorDetail(
            code="invalid_upstream_response",
            message="得理服务返回了无法解析的数据，请改用可信检索源或报告证据不足。",
            retryable=False,
        )
    if isinstance(exc, DelilegalError):
        return ToolErrorDetail(
            code="upstream_error",
            message="得理服务暂时不可用，可稍后重试一次；不要据此编造检索结果。",
            retryable=exc.retryable,
        )
    if isinstance(exc, RetrievalError):
        return ToolErrorDetail(
            code=exc.code,
            message="本地检索暂时不可用，请改用可信检索源或报告证据不足。",
            retryable=exc.retryable,
        )
    return ToolErrorDetail(
        code="tool_internal_error",
        message="检索工具执行失败，请改用其他可信来源或报告证据不足。",
        retryable=False,
    )


def _error_result(
    *,
    trace_id: str,
    source_type: Literal["local_rag", "delilegal_law", "delilegal_case"],
    latency_ms: float,
    exc: Exception,
) -> SearchServiceResult:
    return SearchServiceResult(
        status="error",
        source_type=source_type,
        trace_id=trace_id,
        latency_ms=latency_ms,
        success=False,
        evidence_insufficient=True,
        error=_error_detail(exc),
    )


async def search_law_service(
    params: LawSearchParams,
    *,
    trace_id: str | None = None,
) -> SearchServiceResult:
    """检索正式法规，并压缩为可进入 Agent 上下文的法条摘要。"""
    resolved_trace_id = _resolve_trace_id(trace_id)
    started = _start(resolved_trace_id, "search_law", "delilegal_law")
    try:
        async with DelilegalClient(trace_id=resolved_trace_id) as client:
            response = await client.search_laws(
                LawSearchInput(
                    query=params.query,
                    page_no=params.page_no,
                    page_size=params.top_k,
                    sort_field=params.sort_field,
                    sort_order=params.sort_order,
                )
            )
        values = [
            extract_relevant_articles(item, params.query, max_articles=3, max_chars=1_600)
            for item in response.items[:params.top_k]
        ]
        results, truncated = _bound_results(values, top_k=params.top_k)
        latency_ms = _finish(
            resolved_trace_id,
            "search_law",
            "delilegal_law",
            started,
            success=True,
            result_count=len(results),
        )
        return SearchServiceResult(
            status="found" if results else "no_relevant_result",
            source_type="delilegal_law",
            trace_id=resolved_trace_id,
            latency_ms=latency_ms,
            success=True,
            evidence_insufficient=not results,
            result_count=len(results),
            total_count=response.total_count,
            query_id=response.query_id,
            truncated=truncated or response.total_count > len(results),
            results=results,
        )
    except Exception as exc:
        latency_ms = _finish(
            resolved_trace_id,
            "search_law",
            "delilegal_law",
            started,
            success=False,
            error_type=type(exc).__name__,
        )
        return _error_result(
            trace_id=resolved_trace_id,
            source_type="delilegal_law",
            latency_ms=latency_ms,
            exc=exc,
        )


async def search_case_service(
    params: CaseSearchParams,
    *,
    trace_id: str | None = None,
) -> SearchServiceResult:
    """检索真实类案，并只返回压缩后的事实、争点、裁判理由和结果。"""
    resolved_trace_id = _resolve_trace_id(trace_id)
    started = _start(resolved_trace_id, "search_case", "delilegal_case")
    try:
        request = CaseSearchInput(
            keywords=params.keywords,
            long_text=params.long_text,
            page_no=params.page_no,
            page_size=params.top_k,
            sort_field=params.sort_field,
            sort_order=params.sort_order,
        )
        async with DelilegalClient(trace_id=resolved_trace_id) as client:
            response = await client.search_cases(request)
        query = params.long_text or " ".join(params.keywords or [])
        values = [
            compress_case_content(item, query, max_section_chars=350)
            for item in response.items[:params.top_k]
        ]
        results, truncated = _bound_results(values, top_k=params.top_k)
        latency_ms = _finish(
            resolved_trace_id,
            "search_case",
            "delilegal_case",
            started,
            success=True,
            result_count=len(results),
        )
        return SearchServiceResult(
            status="found" if results else "no_relevant_result",
            source_type="delilegal_case",
            trace_id=resolved_trace_id,
            latency_ms=latency_ms,
            success=True,
            evidence_insufficient=not results,
            result_count=len(results),
            total_count=response.total_count,
            query_id=response.query_id,
            truncated=truncated or response.total_count > len(results),
            results=results,
        )
    except Exception as exc:
        latency_ms = _finish(
            resolved_trace_id,
            "search_case",
            "delilegal_case",
            started,
            success=False,
            error_type=type(exc).__name__,
        )
        return _error_result(
            trace_id=resolved_trace_id,
            source_type="delilegal_case",
            latency_ms=latency_ms,
            exc=exc,
        )


def _local_search_sync(params: LocalLawSearchParams, trace_id: str) -> SearchServiceResult:
    with _LOCAL_RAG_LOCK:
        retriever = LocalLegalRetriever(get_retriever())
        score_threshold = retriever.score_threshold
        scored_chunks = retriever.search(
            params.query,
            top_k=params.top_k,
            trace_id=trace_id,
        )
    if not scored_chunks:
        return SearchServiceResult(
            status="no_relevant_result",
            source_type="local_rag",
            trace_id=trace_id,
            latency_ms=0,
            success=True,
            evidence_insufficient=True,
            score_threshold=score_threshold,
            hint="本地法库未命中。可尝试正式法规检索；若仍无结果，必须报告证据不足。",
        )
    top_score = scored_chunks[0][1]
    low_quality = top_score is not None and float(top_score) < score_threshold
    results = [
        {
            "law_name": chunk.law_name,
            "article_no": chunk.article_no,
            "hierarchy": chunk.hierarchy,
            "content": chunk.content,
            "source_type": SourceType.LOCAL_RAG.value,
            "source_id": chunk.chunk_id,
            "title": chunk.law_name,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "rerank_score": round(float(score), 4),
            "score": round(float(score), 4),
        }
        for chunk, score in scored_chunks[:params.top_k]
    ]
    return SearchServiceResult(
        status="low_quality" if low_quality else "found",
        source_type="local_rag",
        trace_id=trace_id,
        latency_ms=0,
        success=True,
        evidence_insufficient=low_quality,
        result_count=len(results),
        results=results,
        score_threshold=score_threshold,
        top_rerank_score=round(float(top_score), 4) if top_score is not None else None,
        hint=(
            "本地法库最高分低于阈值；可尝试得理（Delilegal）正式法规检索，若仍无结果必须报告证据不足。"
            if low_quality
            else None
        ),
    )


async def search_local_law_service(
    params: LocalLawSearchParams,
    *,
    trace_id: str | None = None,
) -> SearchServiceResult:
    """直接调用进程内 RAG，不经过 MCP；同步模型组件在工作线程中串行执行。"""
    resolved_trace_id = _resolve_trace_id(trace_id)
    started = _start(resolved_trace_id, "search_local_law", "local_rag")
    if len(params.query) < 2:
        exc = ValueError("query must contain at least two non-whitespace characters")
        latency_ms = _finish(
            resolved_trace_id,
            "search_local_law",
            "local_rag",
            started,
            success=False,
            error_type="invalid_input",
        )
        result = _error_result(
            trace_id=resolved_trace_id,
            source_type="local_rag",
            latency_ms=latency_ms,
            exc=exc,
        )
        result.error = ToolErrorDetail(
            code="invalid_input",
            message="检索词至少需要两个非空白字符，请提炼具体法律问题后重试。",
            retryable=False,
        )
        return result
    try:
        result = await asyncio.to_thread(_local_search_sync, params, resolved_trace_id)
        result.latency_ms = _finish(
            resolved_trace_id,
            "search_local_law",
            "local_rag",
            started,
            success=True,
            result_count=result.result_count,
        )
        return result
    except Exception as exc:
        normalized = exc if isinstance(exc, RetrievalError) else RetrievalError(
            "Local legal retrieval failed.",
            retryable=is_retryable_exception(exc),
        )
        latency_ms = _finish(
            resolved_trace_id,
            "search_local_law",
            "local_rag",
            started,
            success=False,
            error_type=type(normalized).__name__,
        )
        return _error_result(
            trace_id=resolved_trace_id,
            source_type="local_rag",
            latency_ms=latency_ms,
            exc=normalized,
        )


__all__ = [
    "CaseSearchParams",
    "LawSearchParams",
    "LocalLawSearchParams",
    "SearchServiceResult",
    "ToolErrorDetail",
    "search_case_service",
    "search_law_service",
    "search_local_law_service",
]
