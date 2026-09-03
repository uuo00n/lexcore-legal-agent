"""会话元数据（Redis 热层）。

只存「非内容」的会话运行时信息：最近活跃时间、请求计数、最近一次 trace_id、
是否携带上传文档。**不存**会话标题、提问原文、回答正文、合同正文（要求 2、4）。

权威数据仍在 PostgreSQL 的会话表里，本模块只是一层可随时丢弃的热数据：
Redis 挂掉时所有读写降级为空 dict / no-op，主链不受影响（要求 1）。
key 一律带 TTL（SESSION_METADATA_TTL_SECONDS，默认 86400s，要求 3）。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from infrastructure.redis import execute, make_key
from services.cache.keys import NAMESPACE_SESSION, digest

log = logging.getLogger("legal.cache.session")

DEFAULT_TTL_SECONDS = 86400

# 允许写入 Redis 的字段白名单：任何不在此列的键都会被丢弃，
# 避免后续调用方顺手把提问原文塞进会话元数据。
ALLOWED_FIELDS = frozenset(
    {
        "last_active_at",
        "request_count",
        "last_trace_id",
        "has_document",
        "provider",
        "model",
    }
)

_INT_FIELDS = ("last_active_at", "request_count")


def cache_enabled() -> bool:
    """
    函数作用：
        判断会话元数据热层是否启用。
    输入参数：
        - 无
    输出参数：
        - bool
    """
    return os.getenv("SESSION_CACHE_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def ttl_seconds() -> int:
    """
    函数作用：
        读取会话元数据 TTL，非法或非正值回退默认值。
    输入参数：
        - 无
    输出参数：
        - int
    """
    raw = os.getenv("SESSION_METADATA_TTL_SECONDS")
    if raw is None or not raw.strip():
        return DEFAULT_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        log.warning("SESSION_METADATA_TTL_SECONDS=%r 不是整数，回退 %s", raw, DEFAULT_TTL_SECONDS)
        return DEFAULT_TTL_SECONDS
    return value if value > 0 else DEFAULT_TTL_SECONDS


def build_key(thread_id: str) -> str:
    """
    函数作用：
        构造会话元数据 key。thread_id 属会话标识，摘要后入 key。
    输入参数：
        - thread_id: str
    输出参数：
        - str
    """
    return make_key(NAMESPACE_SESSION, digest(thread_id))


def _sanitize(fields: dict[str, Any]) -> dict[str, str]:
    """
    函数作用：
        按白名单过滤字段并统一转成字符串，Redis hash 只接受标量。
    输入参数：
        - fields: dict[str, Any]
    输出参数：
        - dict[str, str]
    """
    cleaned: dict[str, str] = {}
    for key, value in fields.items():
        if key not in ALLOWED_FIELDS or value is None:
            continue
        if isinstance(value, bool):
            cleaned[key] = "1" if value else "0"
        else:
            cleaned[key] = str(value)
    return cleaned


async def touch_session(
    thread_id: str,
    *,
    trace_id: str | None = None,
    has_document: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """
    函数作用：
        刷新一次会话元数据：写活跃时间、累加请求计数、续期 TTL。
        用 pipeline 合并为一次往返；Redis 不可用时静默降级。
    输入参数：
        - thread_id: str，空串直接返回
        - trace_id: str | None，默认值 None，本轮 trace 标识
        - has_document: bool | None，默认值 None，仅记录「是否带文档」布尔标记
        - extra: dict[str, Any] | None，默认值 None，其余字段按白名单过滤
    输出参数：
        - 无
    """
    if not cache_enabled() or not thread_id:
        return
    fields: dict[str, Any] = {"last_active_at": int(time.time())}
    if trace_id:
        fields["last_trace_id"] = trace_id
    if has_document is not None:
        fields["has_document"] = has_document
    if extra:
        fields.update(extra)
    payload = _sanitize(fields)
    if not payload:
        return
    key = build_key(thread_id)
    ttl = ttl_seconds()

    async def _action(client: Any) -> Any:
        pipe = client.pipeline()
        pipe.hset(key, mapping=payload)
        pipe.hincrby(key, "request_count", 1)
        pipe.expire(key, ttl)
        return await pipe.execute()

    await execute("session_touch", _action)


async def get_session_metadata(thread_id: str) -> dict[str, Any]:
    """
    函数作用：
        读取会话元数据。Redis 不可用、key 不存在或已过期时返回空 dict，
        调用方必须能在没有热层的情况下工作。
    输入参数：
        - thread_id: str
    输出参数：
        - dict[str, Any]，计数与时间戳已转为 int
    """
    if not cache_enabled() or not thread_id:
        return {}
    key = build_key(thread_id)
    raw = await execute("session_get", lambda client: client.hgetall(key), default={})
    if not isinstance(raw, dict) or not raw:
        return {}
    data: dict[str, Any] = {
        str(field): value for field, value in raw.items() if str(field) in ALLOWED_FIELDS
    }
    for field in _INT_FIELDS:
        if field in data:
            try:
                data[field] = int(data[field])
            except (TypeError, ValueError):
                data.pop(field, None)
    if "has_document" in data:
        data["has_document"] = str(data["has_document"]) in {"1", "true", "True"}
    return data


async def clear_session_metadata(thread_id: str) -> None:
    """
    函数作用：
        删除会话元数据，供会话删除接口调用，避免热层残留已删会话的痕迹。
    输入参数：
        - thread_id: str
    输出参数：
        - 无
    """
    if not thread_id:
        return
    key = build_key(thread_id)
    await execute("session_clear", lambda client: client.delete(key))


__all__ = [
    "ALLOWED_FIELDS",
    "DEFAULT_TTL_SECONDS",
    "build_key",
    "cache_enabled",
    "clear_session_metadata",
    "get_session_metadata",
    "touch_session",
    "ttl_seconds",
]
