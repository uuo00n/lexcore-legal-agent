"""响应缓存（SQLite）。

只做精确问题缓存，避免法律场景中因为模糊匹配导致错误复用。
缓存默认开启，可通过 RESPONSE_CACHE_ENABLED=false 关闭。

本模块刻意留在 SQLite 上：它是主图前的短路分支，语义上属于业务数据而非
可随时丢弃的热缓存，Redis 只承担第十九阶段列出的五类用途。

敏感数据约束：带 doc_id 的回答是针对上传合同/文书生成的，正文可能夹带
合同条款原文，因此使用独立的短 TTL（RESPONSE_CACHE_DOC_TTL_SECONDS，默认 300s），
不做长期缓存。
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Optional

from services.cache.trace import record_cache_event
from services.checkpoint import get_meta_conn

NAMESPACE = "cache:response"


def init_cache_tables() -> None:
    """
    函数作用：
        初始化响应缓存表。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    conn = get_meta_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS response_cache (
            cache_key  TEXT PRIMARY KEY,
            question   TEXT NOT NULL,
            answer     TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )
        """
    )
    conn.commit()


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
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
    conn = get_meta_conn()
    key = make_cache_key(question, doc_id=doc_id)
    now = int(time.time())
    cur = conn.execute(
        "SELECT answer FROM response_cache WHERE cache_key = ? AND expires_at > ?",
        (key, now),
    )
    row = cur.fetchone()
    answer = row[0] if row else None
    record_cache_event(
        trace_id,
        NAMESPACE,
        hit=answer is not None,
        key=key[:32],
        backend="sqlite",
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
    now = int(time.time())
    conn = get_meta_conn()
    conn.execute(
        """INSERT OR REPLACE INTO response_cache
           (cache_key, question, answer, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            make_cache_key(question, doc_id=doc_id),
            question,
            answer,
            now,
            now + _ttl_seconds(doc_id),
        ),
    )
    conn.commit()


__all__ = [
    "NAMESPACE",
    "cache_enabled",
    "get_cached_answer",
    "init_cache_tables",
    "make_cache_key",
    "set_cached_answer",
]
