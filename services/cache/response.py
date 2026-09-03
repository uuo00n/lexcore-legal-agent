"""精确回答缓存（Redis）。

只做精确问题缓存，避免法律场景中因为模糊匹配导致错误复用。
缓存默认开启，可通过 RESPONSE_CACHE_ENABLED=false 关闭。

敏感数据约束：带 doc_id 的回答是针对上传合同/文书生成的，正文可能夹带
合同条款原文，因此使用独立的短 TTL（RESPONSE_CACHE_DOC_TTL_SECONDS，默认 300s），
不做长期缓存。
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional

from infrastructure.redis import execute_sync, make_key as make_redis_key
from services.cache.trace import record_cache_event

NAMESPACE = "cache:response"
_DEGRADED = object()


def cache_enabled() -> bool:
    """
    函数作用：
        判断响应缓存是否启用。
    输入参数：
        - 无
    输出参数：
        - bool
    """
    return os.getenv("RESPONSE_CACHE_ENABLED", "true").lower() not in {"0", "false", "no"}


def _ttl_seconds(doc_id: str | None = None) -> int:
    """
    函数作用：
        读取缓存 TTL。带上传文档的回答使用更短的 TTL，避免长期缓存合同正文。
    输入参数：
        - doc_id: str | None，默认值 None
    输出参数：
        - int
    """
    if doc_id:
        return int(os.getenv("RESPONSE_CACHE_DOC_TTL_SECONDS", "300"))
    return int(os.getenv("RESPONSE_CACHE_TTL_SECONDS", "3600"))


def make_cache_key(question: str, *, doc_id: str | None = None) -> str:
    """
    函数作用：
        生成精确问题缓存 key。原始提问只参与哈希，不出现在 key 中。
    输入参数：
        - question: str
        - doc_id: str | None，默认值 None
    输出参数：
        - str
    """
    normalized = " ".join(question.strip().split())
    raw = f"doc={doc_id or ''}|q={normalized}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return make_redis_key(NAMESPACE, "1", digest)


def get_cached_answer(
    question: str,
    *,
    doc_id: str | None = None,
    trace_id: str | None = None,
) -> Optional[str]:
    """
    函数作用：
        查询未过期的精确问题缓存，并把命中与否记入 trace。
    输入参数：
        - question: str
        - doc_id: str | None，默认值 None
        - trace_id: str | None，默认值 None
    输出参数：
        - Optional[str]
    """
    if not cache_enabled():
        return None
    key = make_cache_key(question, doc_id=doc_id)
    raw = execute_sync("response_cache_get", lambda client: client.get(key), default=_DEGRADED)
    degraded = raw is _DEGRADED
    answer = raw if isinstance(raw, str) else None
    record_cache_event(
        trace_id,
        NAMESPACE,
        hit=answer is not None,
        key=key[:32],
        backend="redis",
        degraded=degraded,
        doc_scoped=bool(doc_id),
    )
    return answer


def set_cached_answer(question: str, answer: str, *, doc_id: str | None = None) -> None:
    """
    函数作用：
        写入响应缓存。带 doc_id 时使用短 TTL，不长期保留合同相关正文。
    输入参数：
        - question: str
        - answer: str
        - doc_id: str | None，默认值 None
    输出参数：
        - 无
    """
    if not cache_enabled() or not answer.strip():
        return
    key = make_cache_key(question, doc_id=doc_id)
    execute_sync(
        "response_cache_set",
        lambda client: client.set(key, answer, ex=_ttl_seconds(doc_id)),
    )


__all__ = [
    "NAMESPACE",
    "cache_enabled",
    "get_cached_answer",
    "make_cache_key",
    "set_cached_answer",
]
