"""Safe tracing, bounding, and error conversion shared by Agent tools."""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from langchain_core.tools import ToolException

from agent.node_utils import record_trace_event
from agent.tools.schemas import MAX_TOOL_OUTPUT_CHARS, RetrievalToolOutput, ToolErrorDetail
from services.delilegal.exceptions import (
    DelilegalAuthenticationError,
    DelilegalConfigurationError,
    DelilegalError,
    DelilegalInvalidResponseError,
    DelilegalTimeoutError,
)

log = logging.getLogger("legal.agent_tools")


def resolve_trace_id(value: str | None) -> str:
    """Always provide a correlation id, including isolated direct tool calls."""
    return value or f"tool-{uuid.uuid4().hex[:16]}"


def start_tool_trace(trace_id: str, tool_name: str, source_type: str) -> float:
    record_trace_event(
        trace_id,
        "tool_start",
        name=tool_name,
        payload={"source_type": source_type},
    )
    return time.perf_counter()


def finish_tool_trace(
    trace_id: str,
    tool_name: str,
    source_type: str,
    started: float,
    *,
    success: bool,
    result_count: int = 0,
    error_type: str | None = None,
) -> float:
    latency_ms = round((time.perf_counter() - started) * 1_000, 2)
    payload = {
        "source_type": source_type,
        "latency_ms": latency_ms,
        "success": success,
        "result_count": result_count,
        "error_type": error_type,
    }
    record_trace_event(trace_id, "tool_end", name=tool_name, payload=payload)
    # Deliberately log metadata only. Queries, headers, appid, and secret never enter logs.
    log.info(
        "agent_tool trace_id=%s tool=%s source_type=%s latency_ms=%s success=%s result_count=%s error_type=%s",
        trace_id,
        tool_name,
        source_type,
        latency_ms,
        success,
        result_count,
        error_type or "-",
    )
    return latency_ms


def bound_results(
    values: list[dict[str, Any]],
    *,
    top_k: int,
    max_chars: int = MAX_TOOL_OUTPUT_CHARS,
) -> tuple[list[dict[str, Any]], bool]:
    """Limit both result count and approximate serialized context size."""
    selected: list[dict[str, Any]] = []
    used = 0
    limited = values[:top_k]
    for value in limited:
        size = len(json.dumps(value, ensure_ascii=False, default=str))
        if selected and used + size > max_chars:
            break
        if not selected and size > max_chars:
            value = _truncate_strings(value, max_string_chars=800)
            size = len(json.dumps(value, ensure_ascii=False, default=str))
        selected.append(value)
        used += size
    return selected, len(selected) < len(values)


def _truncate_strings(value: Any, *, max_string_chars: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_string_chars else value[:max_string_chars] + "…"
    if isinstance(value, list):
        return [_truncate_strings(item, max_string_chars=max_string_chars) for item in value]
    if isinstance(value, dict):
        return {
            key: _truncate_strings(item, max_string_chars=max_string_chars)
            for key, item in value.items()
        }
    return value


def tool_error_output(
    *,
    source_type: str,
    trace_id: str,
    latency_ms: float,
    exc: Exception,
) -> str:
    """Convert service failures to a stable Agent-facing ToolException payload."""
    if isinstance(exc, DelilegalAuthenticationError):
        detail = ToolErrorDetail(
            code="authentication_failed",
            message="得理服务认证失败，请检查服务端配置后重试。",
            retryable=False,
        )
    elif isinstance(exc, DelilegalConfigurationError):
        detail = ToolErrorDetail(
            code="configuration_error",
            message="得理服务尚未正确配置，当前不能使用该检索源。",
            retryable=False,
        )
    elif isinstance(exc, DelilegalTimeoutError):
        detail = ToolErrorDetail(
            code="upstream_timeout",
            message="得理服务响应超时，可稍后重试一次；不要据此编造检索结果。",
            retryable=True,
        )
    elif isinstance(exc, DelilegalInvalidResponseError):
        detail = ToolErrorDetail(
            code="invalid_upstream_response",
            message="得理服务返回了无法解析的数据，请改用可信检索源或报告证据不足。",
            retryable=False,
        )
    elif isinstance(exc, DelilegalError):
        detail = ToolErrorDetail(
            code="upstream_error",
            message="得理服务暂时不可用，可稍后重试一次；不要据此编造检索结果。",
            retryable=True,
        )
    else:
        detail = ToolErrorDetail(
            code="tool_internal_error",
            message="检索工具执行失败，请改用其他可信来源或报告证据不足。",
            retryable=False,
        )
    return RetrievalToolOutput(
        status="error",
        source_type=source_type,  # type: ignore[arg-type]
        trace_id=trace_id,
        latency_ms=latency_ms,
        success=False,
        evidence_insufficient=True,
        error=detail,
    ).model_dump_json(exclude_none=True)


def raise_tool_error(**kwargs: Any) -> None:
    raise ToolException(tool_error_output(**kwargs))


def serialize_tool_exception(error: ToolException) -> str:
    return str(error)
