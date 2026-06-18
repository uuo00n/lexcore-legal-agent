"""用户级配额与限流。

第一版以 thread_id 作为用户/会话标识，提供每日请求数和 token 数限制。
之后如果加入登录系统，可以把 subject 从 thread_id 替换为 user_id。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime

from services.checkpoint import get_meta_conn


@dataclass(frozen=True)
class QuotaDecision:
    """配额检查结果。"""
    allowed: bool
    reason: str
    request_count: int
    token_count: int
    request_limit: int
    token_limit: int


def _today() -> str:
    """
    函数作用：
        返回本地日期字符串。
    输入参数：
        - 无
    输出参数：
        - str
    """
    return datetime.now().strftime("%Y-%m-%d")


def _request_limit() -> int:
    """
    函数作用：
        读取每日请求数限制，0 表示不限制。
    输入参数：
        - 无
    输出参数：
        - int
    """
    return int(os.getenv("LEGAL_DAILY_REQUEST_LIMIT", "200"))


def _token_limit() -> int:
    """
    函数作用：
        读取每日 token 限制，0 表示不限制。
    输入参数：
        - 无
    输出参数：
        - int
    """
    return int(os.getenv("LEGAL_DAILY_TOKEN_LIMIT", "200000"))


def init_quota_tables() -> None:
    """
    函数作用：
        初始化配额表。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    conn = get_meta_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quota_usage (
            subject       TEXT NOT NULL,
            usage_date    TEXT NOT NULL,
            request_count INTEGER NOT NULL DEFAULT 0,
            token_count   INTEGER NOT NULL DEFAULT 0,
            updated_at    INTEGER NOT NULL,
            PRIMARY KEY (subject, usage_date)
        )
        """
    )
    conn.commit()


def get_quota_status(subject: str) -> QuotaDecision:
    """
    函数作用：
        查询 subject 今日配额状态。
    输入参数：
        - subject: str
    输出参数：
        - QuotaDecision
    """
    conn = get_meta_conn()
    cur = conn.execute(
        "SELECT request_count, token_count FROM quota_usage WHERE subject = ? AND usage_date = ?",
        (subject, _today()),
    )
    row = cur.fetchone()
    request_count, token_count = row if row else (0, 0)
    request_limit = _request_limit()
    token_limit = _token_limit()
    if request_limit and request_count >= request_limit:
        allowed = False
        reason = "daily request quota exceeded"
    elif token_limit and token_count >= token_limit:
        allowed = False
        reason = "daily token quota exceeded"
    else:
        allowed = True
        reason = "ok"
    return QuotaDecision(
        allowed=allowed,
        reason=reason,
        request_count=request_count,
        token_count=token_count,
        request_limit=request_limit,
        token_limit=token_limit,
    )


def consume_request(subject: str) -> QuotaDecision:
    """
    函数作用：
        检查并消耗一次请求配额。
    输入参数：
        - subject: str
    输出参数：
        - QuotaDecision
    """
    decision = get_quota_status(subject)
    if not decision.allowed:
        return decision
    conn = get_meta_conn()
    conn.execute(
        """INSERT INTO quota_usage (subject, usage_date, request_count, token_count, updated_at)
           VALUES (?, ?, 1, 0, ?)
           ON CONFLICT(subject, usage_date)
           DO UPDATE SET request_count = request_count + 1, updated_at = excluded.updated_at""",
        (subject, _today(), int(time.time())),
    )
    conn.commit()
    updated = get_quota_status(subject)
    return QuotaDecision(
        allowed=True,
        reason="ok",
        request_count=updated.request_count,
        token_count=updated.token_count,
        request_limit=updated.request_limit,
        token_limit=updated.token_limit,
    )


def add_token_usage(subject: str | None, token_count: int | None) -> None:
    """
    函数作用：
        累加 subject 今日 token 使用量。
    输入参数：
        - subject: str | None
        - token_count: int | None
    输出参数：
        - 无
    """
    if not subject or not token_count:
        return
    conn = get_meta_conn()
    conn.execute(
        """INSERT INTO quota_usage (subject, usage_date, request_count, token_count, updated_at)
           VALUES (?, ?, 0, ?, ?)
           ON CONFLICT(subject, usage_date)
           DO UPDATE SET token_count = token_count + excluded.token_count,
                         updated_at = excluded.updated_at""",
        (subject, _today(), token_count, int(time.time())),
    )
    conn.commit()


def list_quota_usage(limit: int = 50) -> list[dict]:
    """
    函数作用：
        查询最近配额使用情况。
    输入参数：
        - limit: int，默认值 50
    输出参数：
        - list[dict]
    """
    conn = get_meta_conn()
    cur = conn.execute(
        """SELECT subject, usage_date, request_count, token_count, updated_at
           FROM quota_usage
           ORDER BY updated_at DESC
           LIMIT ?""",
        (limit,),
    )
    return [
        {
            "subject": row[0],
            "usage_date": row[1],
            "request_count": row[2],
            "token_count": row[3],
            "updated_at": row[4],
            "request_limit": _request_limit(),
            "token_limit": _token_limit(),
        }
        for row in cur.fetchall()
    ]
