"""响应缓存。

第一版只做精确问题缓存，避免法律场景中因为模糊匹配导致错误复用。
缓存默认开启，可通过 RESPONSE_CACHE_ENABLED=false 关闭。
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Optional

from services.checkpoint import get_meta_conn


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


def _ttl_seconds() -> int:
    """
    函数作用：
        读取缓存 TTL。
    输入参数：
        - 无
    输出参数：
        - int
    """
    return int(os.getenv("RESPONSE_CACHE_TTL_SECONDS", "3600"))


def make_cache_key(question: str, *, doc_id: str | None = None) -> str:
    """
    函数作用：
        生成精确问题缓存 key。
    输入参数：
        - question: str
        - doc_id: str | None，默认值 None
    输出参数：
        - str
    """
    normalized = " ".join(question.strip().split())
    raw = f"doc={doc_id or ''}|q={normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_answer(question: str, *, doc_id: str | None = None) -> Optional[str]:
    """
    函数作用：
        查询未过期的精确问题缓存。
    输入参数：
        - question: str
        - doc_id: str | None，默认值 None
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
    return row[0] if row else None


def set_cached_answer(question: str, answer: str, *, doc_id: str | None = None) -> None:
    """
    函数作用：
        写入响应缓存。
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
        (make_cache_key(question, doc_id=doc_id), question, answer, now, now + _ttl_seconds()),
    )
    conn.commit()
