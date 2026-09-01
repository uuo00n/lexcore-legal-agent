"""Agent Tool 运行时适配；业务执行与错误模型位于 Service Layer。"""
from __future__ import annotations

import uuid

from langchain_core.tools import ToolException


def resolve_trace_id(value: str | None) -> str:
    """为独立 Tool 调用补充关联标识。"""
    return value or f"tool-{uuid.uuid4().hex[:16]}"


def serialize_tool_exception(error: ToolException) -> str:
    """保持 Service 产生的安全 JSON 错误结构。"""
    return str(error)


__all__ = ["resolve_trace_id", "serialize_tool_exception"]
