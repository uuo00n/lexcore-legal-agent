"""幂等标记（Redis SET NX EX）。

用途：客户端重试、SSE 断线重连、上传重复提交时避免同一请求被执行两次。
调用方先 `claim()` 抢占一个幂等 key，拿到 `acquired=True` 才继续执行业务；
执行成功后 `mark_completed()`，失败则 `release()` 让重试能够重新抢占。

安全约束：
- 幂等 token 由调用方提供（可能是客户端请求 ID 或提问指纹），一律摘要后入 key（要求 2）。
- 记录值只放状态、时间戳和一个非敏感引用（如 trace_id），**不放回答正文或合同内容**（要求 4）。
- key 一律带 TTL（IDEMPOTENCY_TTL_SECONDS，默认 600s，要求 3）。
- Redis 不可用时 `claim()` 返回 acquired=True 且 degraded=True：
  宁可重复执行一次，也不能因为缓存层故障拒绝用户请求（要求 1）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from infrastructure.redis import execute, make_key
from services.cache.keys import NAMESPACE_IDEMPOTENCY, digest

log = logging.getLogger("legal.cache.idempotency")

DEFAULT_TTL_SECONDS = 600

STATE_IN_PROGRESS = "in_progress"
STATE_COMPLETED = "completed"

# 区分「Redis 降级」与「key 不存在」，见 services/cache/retrieval.py 同名说明。
_DEGRADED = object()


@dataclass(frozen=True)
class IdempotencyClaim:
    """一次幂等抢占结果。"""

    key: str
    acquired: bool
    degraded: bool = False
    record: dict[str, Any] | None = None

    @property
    def duplicate(self) -> bool:
        """
        函数作用：
            是否确认为重复请求（抢占失败且 Redis 正常）。
        输入参数：
            - 无
        输出参数：
            - bool
        """
        return not self.acquired and not self.degraded

    @property
    def completed(self) -> bool:
        """
        函数作用：
            重复请求对应的原请求是否已执行完成。
        输入参数：
            - 无
        输出参数：
            - bool
        """
        return bool(self.record) and self.record.get("state") == STATE_COMPLETED


def idempotency_enabled() -> bool:
    """
    函数作用：
        判断幂等标记是否启用。
    输入参数：
        - 无
    输出参数：
        - bool
    """
    return os.getenv("IDEMPOTENCY_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def ttl_seconds() -> int:
    """
    函数作用：
        读取幂等标记 TTL，非法或非正值回退默认值。
    输入参数：
        - 无
    输出参数：
        - int
    """
    raw = os.getenv("IDEMPOTENCY_TTL_SECONDS")
    if raw is None or not raw.strip():
        return DEFAULT_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        log.warning("IDEMPOTENCY_TTL_SECONDS=%r 不是整数，回退 %s", raw, DEFAULT_TTL_SECONDS)
        return DEFAULT_TTL_SECONDS
    return value if value > 0 else DEFAULT_TTL_SECONDS


def build_key(scope: str, token: str) -> str:
    """
    函数作用：
        构造幂等 key：`idempotency:{scope}:{token 摘要}`。
    输入参数：
        - scope: str，业务场景，如 chat / upload
        - token: str，幂等标识，仅参与摘要
    输出参数：
        - str
    """
    return make_key(NAMESPACE_IDEMPOTENCY, scope, digest(token))


def _encode(state: str, ref: str = "") -> str:
    """
    函数作用：
        序列化幂等记录。ref 只允许放非敏感引用（trace_id 等）。
    输入参数：
        - state: str
        - ref: str，默认值 ''
    输出参数：
        - str
    """
    return json.dumps({"state": state, "at": int(time.time()), "ref": ref}, ensure_ascii=False)


def _decode(raw: Any) -> dict[str, Any] | None:
    """
    函数作用：
        反序列化幂等记录，非法内容视为不存在。
    输入参数：
        - raw: Any
    输出参数：
        - dict[str, Any] | None
    """
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


async def claim(
    scope: str,
    token: str,
    *,
    ref: str = "",
    ttl: int | None = None,
) -> IdempotencyClaim:
    """
    函数作用：
        以 SET NX EX 抢占幂等 key。抢占成功表示本次是首次执行；
        失败则回读已有记录，供调用方判断原请求是否已完成。
    输入参数：
        - scope: str
        - token: str，空串视为不做幂等
        - ref: str，默认值 ''，非敏感引用，如 trace_id
        - ttl: int | None，默认值 None 表示读环境变量
    输出参数：
        - IdempotencyClaim
    """
    key = build_key(scope, token) if token else ""
    if not idempotency_enabled() or not token:
        return IdempotencyClaim(key=key, acquired=True)

    expire = ttl if ttl is not None and ttl > 0 else ttl_seconds()
    payload = _encode(STATE_IN_PROGRESS, ref)
    acquired = await execute(
        "idempotency_claim",
        lambda client: client.set(key, payload, nx=True, ex=expire),
        default=_DEGRADED,
    )
    if acquired is _DEGRADED:
        return IdempotencyClaim(key=key, acquired=True, degraded=True)
    if acquired:
        return IdempotencyClaim(key=key, acquired=True)

    raw = await execute("idempotency_get", lambda client: client.get(key))
    return IdempotencyClaim(key=key, acquired=False, record=_decode(raw))


async def mark_completed(
    scope: str,
    token: str,
    *,
    ref: str = "",
    ttl: int | None = None,
) -> None:
    """
    函数作用：
        把幂等记录标记为已完成并重置 TTL，让 TTL 窗口内的重试能识别出重复。
    输入参数：
        - scope: str
        - token: str
        - ref: str，默认值 ''
        - ttl: int | None，默认值 None
    输出参数：
        - 无
    """
    if not idempotency_enabled() or not token:
        return
    key = build_key(scope, token)
    expire = ttl if ttl is not None and ttl > 0 else ttl_seconds()
    payload = _encode(STATE_COMPLETED, ref)
    await execute(
        "idempotency_complete",
        lambda client: client.set(key, payload, ex=expire),
    )


async def release(scope: str, token: str) -> None:
    """
    函数作用：
        删除幂等记录。业务执行失败时调用，避免一次失败把该请求锁死到 TTL 到期。
    输入参数：
        - scope: str
        - token: str
    输出参数：
        - 无
    """
    if not token:
        return
    key = build_key(scope, token)
    await execute("idempotency_release", lambda client: client.delete(key))


async def get_record(scope: str, token: str) -> dict[str, Any] | None:
    """
    函数作用：
        读取幂等记录，供排查与管理接口使用。
    输入参数：
        - scope: str
        - token: str
    输出参数：
        - dict[str, Any] | None
    """
    if not token:
        return None
    raw = await execute("idempotency_get", lambda client: client.get(build_key(scope, token)))
    return _decode(raw)


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "STATE_COMPLETED",
    "STATE_IN_PROGRESS",
    "IdempotencyClaim",
    "build_key",
    "claim",
    "get_record",
    "idempotency_enabled",
    "mark_completed",
    "release",
    "ttl_seconds",
]
