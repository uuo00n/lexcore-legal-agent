"""得理（Delilegal）API 响应缓存（Redis，异步）。

得理法规检索与类案检索是外部计费接口，同一检索条件在多轮对话中会重复触发，
因此按「endpoint_type + 请求体指纹」缓存上游原始 JSON。

安全约束：
- key 只有 endpoint_type 与 `fingerprint(payload)`，检索词不进 key（要求 2）。
- 只缓存响应体；Authorization Bearer API Key 在 header 中，永不进入缓存值。
- 一律带 TTL（DELILEGAL_CACHE_TTL_SECONDS，默认 3600s，要求 3）。
- Redis 不可用时读写都降级为 no-op，直接打真实接口（要求 1）。
- 只缓存成功响应；失败与异常不写缓存，避免把一次抖动固化一小时。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from infrastructure.redis import execute, make_key
from services.cache.keys import NAMESPACE_DELILEGAL, fingerprint
from services.cache.trace import record_cache_event

log = logging.getLogger("legal.cache.delilegal")

DEFAULT_TTL_SECONDS = 3600

# 区分「Redis 降级」与「key 不存在」，见 services/cache/retrieval.py 同名说明。
_DEGRADED = object()


def cache_enabled() -> bool:
    """
    函数作用：
        判断得理响应缓存是否启用。
    输入参数：
        - 无
    输出参数：
        - bool
    """
    return os.getenv("DELILEGAL_CACHE_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def ttl_seconds() -> int:
    """
    函数作用：
        读取得理响应缓存 TTL，非法或非正值回退默认值。
    输入参数：
        - 无
    输出参数：
        - int
    """
    raw = os.getenv("DELILEGAL_CACHE_TTL_SECONDS")
    if raw is None or not raw.strip():
        return DEFAULT_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        log.warning("DELILEGAL_CACHE_TTL_SECONDS=%r 不是整数，回退 %s", raw, DEFAULT_TTL_SECONDS)
        return DEFAULT_TTL_SECONDS
    return value if value > 0 else DEFAULT_TTL_SECONDS


def build_key(endpoint_type: str, payload: Any) -> str:
    """
    函数作用：
        构造得理响应缓存 key。endpoint_type 是固定枚举可明文入 key，
        请求体（含检索词）一律取排序 JSON 指纹。
    输入参数：
        - endpoint_type: str，如 law_search / case_search
        - payload: Any，请求体
    输出参数：
        - str
    """
    return make_key(NAMESPACE_DELILEGAL, endpoint_type, fingerprint(payload))


async def get_cached_response(
    endpoint_type: str,
    payload: Any,
    *,
    trace_id: str | None = None,
) -> Any | None:
    """
    函数作用：
        查询缓存的得理响应，并写入 cache_hit / cache_miss trace（要求 5）。
        返回 None 表示需要真正请求上游。
    输入参数：
        - endpoint_type: str
        - payload: Any
        - trace_id: str | None，默认值 None
    输出参数：
        - Any | None，上游原始 JSON
    """
    if not cache_enabled():
        return None
    key = build_key(endpoint_type, payload)
    raw = await execute("delilegal_cache_get", lambda client: client.get(key), default=_DEGRADED)
    degraded = raw is _DEGRADED
    value: Any | None = None
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            value = None
    record_cache_event(
        trace_id,
        NAMESPACE_DELILEGAL,
        hit=value is not None,
        key=key,
        degraded=degraded,
        endpoint_type=endpoint_type,
    )
    return value


async def set_cached_response(endpoint_type: str, payload: Any, response: Any) -> None:
    """
    函数作用：
        写入得理响应缓存。不可 JSON 序列化的响应直接跳过。
    输入参数：
        - endpoint_type: str
        - payload: Any，请求体
        - response: Any，上游原始 JSON
    输出参数：
        - 无
    """
    if not cache_enabled() or response is None:
        return
    try:
        serialized = json.dumps(response, ensure_ascii=False)
    except (TypeError, ValueError):
        log.debug("delilegal 响应不可序列化，跳过缓存: endpoint_type=%s", endpoint_type)
        return
    key = build_key(endpoint_type, payload)
    ttl = ttl_seconds()
    await execute(
        "delilegal_cache_set",
        lambda client: client.set(key, serialized, ex=ttl),
    )


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "build_key",
    "cache_enabled",
    "get_cached_response",
    "set_cached_response",
    "ttl_seconds",
]
