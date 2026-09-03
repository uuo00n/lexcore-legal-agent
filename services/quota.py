"""用户级配额与限流。

第一版以 thread_id 作为用户/会话标识，提供每日请求数和 token 数限制。
之后如果加入登录系统，可以把 subject 从 thread_id 替换为 user_id。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime

from infrastructure.operational_store import get_operational_store


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


def get_quota_status(subject: str) -> QuotaDecision:
    """
    函数作用：
        查询 subject 今日配额状态。
    输入参数：
        - subject: str
    输出参数：
        - QuotaDecision
    """
    row = get_operational_store().get_quota(subject, _today())
    request_count = int(row["request_count"]) if row else 0
    token_count = int(row["token_count"]) if row else 0
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
    request_limit = _request_limit()
    token_limit = _token_limit()
    row = get_operational_store().consume_quota(
        subject,
        _today(),
        request_limit,
        token_limit,
        int(time.time()),
    )
    request_count = int(row["request_count"])
    token_count = int(row["token_count"])
    if not row["consumed"]:
        reason = (
            "daily request quota exceeded"
            if request_limit and request_count >= request_limit
            else "daily token quota exceeded"
        )
        return QuotaDecision(
            allowed=False,
            reason=reason,
            request_count=request_count,
            token_count=token_count,
            request_limit=request_limit,
            token_limit=token_limit,
        )
    return QuotaDecision(
        allowed=True,
        reason="ok",
        request_count=request_count,
        token_count=token_count,
        request_limit=request_limit,
        token_limit=token_limit,
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
    get_operational_store().add_token_usage(
        subject,
        _today(),
        token_count,
        int(time.time()),
    )


def list_quota_usage(limit: int = 50) -> list[dict]:
    """
    函数作用：
        查询最近配额使用情况。
    输入参数：
        - limit: int，默认值 50
    输出参数：
        - list[dict]
    """
    rows = get_operational_store().list_quota(limit)
    return [
        {
            "subject": row["subject"],
            "usage_date": str(row["usage_date"]),
            "request_count": row["request_count"],
            "token_count": row["token_count"],
            "updated_at": row["updated_at"],
            "request_limit": _request_limit(),
            "token_limit": _token_limit(),
        }
        for row in rows
    ]
