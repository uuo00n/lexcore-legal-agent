"""只重试临时故障的同步/异步指数退避策略。"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from tenacity import AsyncRetrying, Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from services.errors import LegalServiceError

log = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """传输层重试参数；一次初始调用也计入 max_attempts。"""

    max_attempts: int = 3
    multiplier: float = 0.25
    min_wait: float = 0.25
    max_wait: float = 2.0


DEFAULT_RETRY_POLICY = RetryPolicy()


def _status_code(error: BaseException) -> int | None:
    value = getattr(error, "status_code", None)
    if value is None:
        response = getattr(error, "response", None)
        value = getattr(response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def is_retryable_exception(error: BaseException) -> bool:
    """仅识别 timeout、临时连接错误和 HTTP 5xx。"""

    for current in _exception_chain(error):
        if isinstance(current, LegalServiceError):
            return current.retryable

        status_code = _status_code(current)
        if status_code is not None:
            return 500 <= status_code <= 599

        if isinstance(current, (TimeoutError, ConnectionError)):
            return True

        if isinstance(
            current,
            (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.ReadError,
                httpx.WriteError,
                httpx.RemoteProtocolError,
                httpx.PoolTimeout,
            ),
        ):
            return True

        if bool(getattr(current, "connection_invalidated", False)):
            return True

    return False


def _before_sleep(operation_name: str) -> Callable[[Any], None]:
    def log_retry(state: Any) -> None:
        error = state.outcome.exception() if state.outcome else None
        log.warning(
            "临时故障，准备指数退避重试: operation=%s attempt=%s/%s error_type=%s",
            operation_name,
            state.attempt_number,
            state.retry_object.stop.max_attempt_number,
            type(error).__name__ if error else "unknown",
        )

    return log_retry


def _common_kwargs(policy: RetryPolicy, operation_name: str) -> dict[str, Any]:
    return {
        "retry": retry_if_exception(is_retryable_exception),
        "stop": stop_after_attempt(max(1, policy.max_attempts)),
        "wait": wait_exponential(
            multiplier=max(0.0, policy.multiplier),
            min=max(0.0, policy.min_wait),
            max=max(0.0, policy.max_wait),
        ),
        "before_sleep": _before_sleep(operation_name),
        "reraise": True,
    }


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    operation_name: str,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> T:
    """执行异步操作；非临时错误不会进入第二次尝试。"""

    retrying = AsyncRetrying(**_common_kwargs(policy, operation_name))
    return await retrying(operation)


def retry_sync(
    operation: Callable[[], T],
    *,
    operation_name: str,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> T:
    """执行同步操作；语义与 retry_async 一致。"""

    retrying = Retrying(**_common_kwargs(policy, operation_name))
    return retrying(operation)


__all__ = [
    "DEFAULT_RETRY_POLICY",
    "RetryPolicy",
    "is_retryable_exception",
    "retry_async",
    "retry_sync",
]
