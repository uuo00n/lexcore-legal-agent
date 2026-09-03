"""请求限流（Redis 固定窗口计数）。

与 `services/quota.py` 的关系：配额是「每天多少次 / 多少 token」的业务额度，
落在 PostgreSQL 上是权威记录；本模块是「每分钟多少次」的突发保护，只在 Redis 上做。
两者互补，Redis 挂掉时限流失效但配额仍然生效，因此不会失去全部保护。

失败策略：**fail-open**。Redis 不可用时一律放行（要求 1），
宁可短时间放过突发流量，也不能因为缓存层故障让法律咨询主链整体不可用。

安全约束：
- subject（thread_id / IP 等）经 `digest()` 摘要后入 key，明文不落 Redis（要求 2）。
- 计数 key 一律带 TTL，等于窗口长度（要求 3）。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from infrastructure.redis import execute, make_key
from services.cache.keys import NAMESPACE_RATE_LIMIT, digest

log = logging.getLogger("legal.cache.ratelimit")

DEFAULT_LIMIT = 30
DEFAULT_WINDOW_SECONDS = 60

# INCR 与首次 EXPIRE 必须在 Redis 端原子执行：若拆成两次网络往返，
# Redis 恰好在两条命令之间故障会留下没有 TTL 的永久计数 key。
# 脚本同时返回 TTL，超限分支无需再发第三条命令。
_INCR_WITH_TTL_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {count, ttl}
"""


@dataclass(frozen=True)
class RateLimitDecision:
    """一次限流判定结果；`degraded=True` 表示 Redis 不可用而非真的未超限。"""

    allowed: bool
    limit: int
    remaining: int
    window_seconds: int
    count: int = 0
    retry_after: int = 0
    degraded: bool = False

    @property
    def reason(self) -> str:
        """
        函数作用：
            返回可直接放进 429 响应的原因说明，不含 subject 明文。
        输入参数：
            - 无
        输出参数：
            - str
        """
        if self.allowed:
            return ""
        return f"请求过于频繁：{self.window_seconds} 秒内最多 {self.limit} 次，请稍后重试。"


def rate_limit_enabled() -> bool:
    """
    函数作用：
        判断限流是否启用。
    输入参数：
        - 无
    输出参数：
        - bool
    """
    return os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _env_positive_int(key: str, default: int) -> int:
    """
    函数作用：
        读取正整数环境变量，非法或非正值回退默认值。
    输入参数：
        - key: str
        - default: int
    输出参数：
        - int
    """
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("环境变量 %s=%r 不是整数，回退默认值 %s", key, raw, default)
        return default
    return value if value > 0 else default


def default_limit() -> int:
    """
    函数作用：
        返回窗口内允许的最大请求数。
    输入参数：
        - 无
    输出参数：
        - int
    """
    return _env_positive_int("RATE_LIMIT_REQUESTS", DEFAULT_LIMIT)


def default_window() -> int:
    """
    函数作用：
        返回限流窗口长度（秒），同时作为计数 key 的 TTL。
    输入参数：
        - 无
    输出参数：
        - int
    """
    return _env_positive_int("RATE_LIMIT_WINDOW_SECONDS", DEFAULT_WINDOW_SECONDS)


def build_key(subject: str, scope: str, window_seconds: int) -> str:
    """
    函数作用：
        构造限流计数 key：`ratelimit:{scope}:{window}:{subject 摘要}`。
        窗口长度进 key，调整配置后不会继承旧窗口的计数。
    输入参数：
        - subject: str，限流主体（thread_id / IP 等），仅参与摘要
        - scope: str，限流场景，如 chat / upload
        - window_seconds: int
    输出参数：
        - str
    """
    return make_key(NAMESPACE_RATE_LIMIT, scope, str(window_seconds), digest(subject))


def _record(scope: str, outcome: str) -> None:
    """
    函数作用：
        为限流判定打点，便于观察 allow / block / degraded 比例。
    输入参数：
        - scope: str
        - outcome: str，allow / block / degraded / disabled
    输出参数：
        - 无
    """
    try:
        from services.metrics import inc_counter

        inc_counter("legal_rate_limit_decisions_total", {"scope": scope, "outcome": outcome})
    except Exception:  # noqa: BLE001 - 打点失败不得影响主链路
        pass


def _record_blocked(trace_id: str | None, scope: str, decision: RateLimitDecision) -> None:
    """
    函数作用：
        把被限流的请求写入 trace，便于事后定位 429 来源。payload 不含 subject 明文。
    输入参数：
        - trace_id: str | None
        - scope: str
        - decision: RateLimitDecision
    输出参数：
        - 无
    """
    if not trace_id:
        return
    try:
        from services.observability import record_event

        record_event(
            trace_id,
            "rate_limited",
            name=f"{NAMESPACE_RATE_LIMIT}:{scope}",
            payload={
                "scope": scope,
                "limit": decision.limit,
                "window_seconds": decision.window_seconds,
                "count": decision.count,
                "retry_after": decision.retry_after,
            },
        )
    except Exception as exc:  # noqa: BLE001 - trace 故障不影响限流
        log.debug("rate limit trace event skipped: %s", exc)


async def check_rate_limit(
    subject: str,
    *,
    scope: str = "chat",
    limit: int | None = None,
    window_seconds: int | None = None,
    trace_id: str | None = None,
) -> RateLimitDecision:
    """
    函数作用：
        对 subject 做一次固定窗口计数并判定是否放行。
        Redis 不可用时返回 allowed=True 且 degraded=True（fail-open，要求 1）。
    输入参数：
        - subject: str，限流主体，空串视为不限流
        - scope: str，默认值 'chat'
        - limit: int | None，默认值 None 表示读环境变量
        - window_seconds: int | None，默认值 None 表示读环境变量
        - trace_id: str | None，默认值 None
    输出参数：
        - RateLimitDecision
    """
    max_requests = limit if limit is not None else default_limit()
    window = window_seconds if window_seconds is not None else default_window()
    if not rate_limit_enabled() or not subject or max_requests <= 0:
        _record(scope, "disabled")
        return RateLimitDecision(
            allowed=True,
            limit=max_requests,
            remaining=max_requests,
            window_seconds=window,
        )

    key = build_key(subject, scope, window)
    result = await execute(
        "rate_limit_check",
        lambda client: client.eval(_INCR_WITH_TTL_SCRIPT, 1, key, window),
    )
    if not isinstance(result, (list, tuple)) or len(result) != 2:
        _record(scope, "degraded")
        return RateLimitDecision(
            allowed=True,
            limit=max_requests,
            remaining=max_requests,
            window_seconds=window,
            degraded=True,
        )

    try:
        count = int(result[0])
        current_ttl = int(result[1])
    except (TypeError, ValueError):
        # Redis 返回异常形状时 fail-open，缓存层不能阻断 Agent 主链。
        _record(scope, "degraded")
        return RateLimitDecision(
            allowed=True,
            limit=max_requests,
            remaining=max_requests,
            window_seconds=window,
            degraded=True,
        )

    if count <= max_requests:
        _record(scope, "allow")
        return RateLimitDecision(
            allowed=True,
            limit=max_requests,
            remaining=max_requests - count,
            window_seconds=window,
            count=count,
        )

    retry_after = current_ttl if current_ttl > 0 else window
    decision = RateLimitDecision(
        allowed=False,
        limit=max_requests,
        remaining=0,
        window_seconds=window,
        count=count,
        retry_after=retry_after,
    )
    _record(scope, "block")
    _record_blocked(trace_id, scope, decision)
    return decision


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_WINDOW_SECONDS",
    "RateLimitDecision",
    "build_key",
    "check_rate_limit",
    "default_limit",
    "default_window",
    "rate_limit_enabled",
]
