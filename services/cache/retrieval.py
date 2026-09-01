"""检索结果缓存（Redis，同步）。

Hybrid RAG 一次完整召回要付 embedding + BM25 + reranker 三段开销，
同一问题在多轮对话与并发请求里会被反复检索，因此在管线最外层加一层缓存。

同步实现的原因：`services/rag/retriever.py` 全链路同步，且运行在 MCP stdio
子进程内，没有可用的事件循环，所以统一走 `infrastructure.redis.execute_sync`。

安全约束：
- key 只包含 `digest(归一化 query + 检索参数)`，原始提问不进 key（要求 2）。
- 缓存值只有法条召回结果（法条正文属公开语料），不回写用户提问本身。
- 一律带 TTL（RETRIEVAL_CACHE_TTL_SECONDS，默认 1800s，要求 3）。
- Redis 不可用时 `execute_sync` 返回默认值，检索照常执行（要求 1）。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from infrastructure.redis import execute_sync, make_key
from services.cache.keys import NAMESPACE_RETRIEVAL, digest, fingerprint, normalize_text
from services.cache.trace import record_cache_event
from services.rag.interfaces import DocumentResult, LawChunk

log = logging.getLogger("legal.cache.retrieval")

# 缓存值格式版本；结构变更时递增即可让旧条目自然失效。
SCHEMA_VERSION = 2

DEFAULT_TTL_SECONDS = 1800

# 用于区分「Redis 降级」与「真的没有这个 key」：GET 未命中返回 None，
# 降级时 execute_sync 返回该哨兵，trace 里才能把两者分开统计。
_DEGRADED = object()


def cache_enabled() -> bool:
    """
    函数作用：
        判断检索缓存是否启用。
    输入参数：
        - 无
    输出参数：
        - bool
    """
    return os.getenv("RETRIEVAL_CACHE_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def ttl_seconds() -> int:
    """
    函数作用：
        读取检索缓存 TTL，非法或非正值回退默认值，保证一定带过期时间。
    输入参数：
        - 无
    输出参数：
        - int
    """
    raw = os.getenv("RETRIEVAL_CACHE_TTL_SECONDS")
    if raw is None or not raw.strip():
        return DEFAULT_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        log.warning("RETRIEVAL_CACHE_TTL_SECONDS=%r 不是整数，回退 %s", raw, DEFAULT_TTL_SECONDS)
        return DEFAULT_TTL_SECONDS
    return value if value > 0 else DEFAULT_TTL_SECONDS


def build_key(query: str, params: dict[str, Any] | None = None) -> str:
    """
    函数作用：
        构造检索缓存 key。query 先归一化再摘要，检索参数取结构化指纹，
        因此调整 TopK / 阈值 / reranker 开关都不会命中旧结果。
    输入参数：
        - query: str，用户原始查询
        - params: dict[str, Any] | None，默认值 None，影响召回结果的检索参数
    输出参数：
        - str，完整 Redis key
    """
    return make_key(
        NAMESPACE_RETRIEVAL,
        str(SCHEMA_VERSION),
        digest(normalize_text(query)),
        fingerprint(params or {}),
    )


def _dump(results: list[DocumentResult], reranked: bool) -> str:
    """
    函数作用：
        把召回结果序列化为 JSON 字符串。
    输入参数：
        - results: list[DocumentResult]
        - reranked: bool，是否经过精排（决定调用方是否套用分数阈值）
    输出参数：
        - str
    """
    payload = {
        "v": SCHEMA_VERSION,
        "reranked": bool(reranked),
        "results": [
            {
                "score": float(score),
                "doc": {
                    "law_name": document.law_name,
                    "hierarchy": document.hierarchy,
                    "article_no": document.article_no,
                    "content": document.content,
                    "chunk_id": document.chunk_id,
                    "metadata": document.metadata or {},
                },
            }
            for document, score in results
        ],
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def _load(raw: str) -> tuple[list[DocumentResult], bool] | None:
    """
    函数作用：
        反序列化缓存值；格式不符或版本不匹配时视为未命中。
    输入参数：
        - raw: str
    输出参数：
        - tuple[list[DocumentResult], bool] | None
    """
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("v") != SCHEMA_VERSION:
        return None
    items = payload.get("results")
    if not isinstance(items, list):
        return None
    results: list[DocumentResult] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        doc = item.get("doc")
        if not isinstance(doc, dict):
            return None
        metadata = doc.get("metadata")
        results.append(
            DocumentResult(
                LawChunk(
                    law_name=str(doc.get("law_name") or ""),
                    hierarchy=str(doc.get("hierarchy") or ""),
                    article_no=str(doc.get("article_no") or ""),
                    content=str(doc.get("content") or ""),
                    chunk_id=str(doc.get("chunk_id") or ""),
                    metadata=dict(metadata) if isinstance(metadata, dict) else {},
                ),
                float(item.get("score") or 0.0),
            )
        )
    return results, bool(payload.get("reranked"))


def get_cached_results(
    query: str,
    params: dict[str, Any] | None = None,
    *,
    trace_id: str | None = None,
) -> tuple[list[DocumentResult], bool] | None:
    """
    函数作用：
        查询检索缓存，并把命中与否写入 trace（要求 5）。
        返回 None 表示需要真正执行检索，Redis 故障与未命中都走这条路径。
    输入参数：
        - query: str
        - params: dict[str, Any] | None，默认值 None
        - trace_id: str | None，默认值 None
    输出参数：
        - tuple[list[DocumentResult], bool] | None，(结果, 是否已精排)
    """
    if not cache_enabled():
        return None
    key = build_key(query, params)
    raw = execute_sync("retrieval_cache_get", lambda client: client.get(key), default=_DEGRADED)
    degraded = raw is _DEGRADED
    loaded = _load(raw) if isinstance(raw, str) else None
    record_cache_event(
        trace_id,
        NAMESPACE_RETRIEVAL,
        hit=loaded is not None,
        key=key,
        degraded=degraded,
        result_count=len(loaded[0]) if loaded else 0,
    )
    return loaded


def set_cached_results(
    query: str,
    results: list[DocumentResult],
    *,
    reranked: bool,
    params: dict[str, Any] | None = None,
) -> None:
    """
    函数作用：
        写入检索缓存。空结果不缓存，避免索引尚未就绪时把空召回固化 TTL 时长。
    输入参数：
        - query: str
        - results: list[DocumentResult]
        - reranked: bool
        - params: dict[str, Any] | None，默认值 None
    输出参数：
        - 无
    """
    if not cache_enabled() or not results:
        return
    key = build_key(query, params)
    payload = _dump(results, reranked)
    ttl = ttl_seconds()
    execute_sync("retrieval_cache_set", lambda client: client.set(key, payload, ex=ttl))


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "SCHEMA_VERSION",
    "build_key",
    "cache_enabled",
    "get_cached_results",
    "set_cached_results",
    "ttl_seconds",
]
