"""缓存命中的 trace 记录与指标打点。

所有缓存模块统一经此写入 `cache_hit` / `cache_miss` 事件，因此可以在
`/api/admin/traces/{trace_id}` 时间线上直接看到某一轮到底命中了哪些缓存。

payload 只放命名空间、key 摘要、TTL 和降级标记，不放缓存值本身：
检索结果、得理响应和答案正文都不进 trace。
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("legal.cache")


def record_cache_event(
    trace_id: str | None,
    namespace: str,
    *,
    hit: bool,
    key: str = "",
    backend: str = "redis",
    degraded: bool = False,
    **details: Any,
) -> None:
    """
    函数作用：
        记录一次缓存查询结果。可观测性故障不得影响缓存与主链路，因此整体兜底。
    输入参数：
        - trace_id: str | None，为空时只打点不写 trace
        - namespace: str，缓存命名空间，如 cache:retrieval
        - hit: bool，是否命中
        - key: str，默认值 ''，已摘要化的 key，可安全落库
        - backend: str，默认值 'redis'
        - degraded: bool，默认值 False，True 表示缓存后端不可用而非真的未命中
        - details: Any，附加的非敏感字段，如 ttl / result_count
    输出参数：
        - 无
    """
    try:
        from services.metrics import inc_counter

        outcome = "degraded" if degraded else ("hit" if hit else "miss")
        inc_counter("legal_cache_lookups_total", {"namespace": namespace, "outcome": outcome})
    except Exception:  # noqa: BLE001 - 打点失败静默
        pass

    if not trace_id:
        return
    try:
        from services.observability import record_event

        record_event(
            trace_id,
            "cache_hit" if hit else "cache_miss",
            name=namespace,
            payload={
                "namespace": namespace,
                "key": key,
                "backend": backend,
                "degraded": degraded,
                **details,
            },
        )
    except Exception as exc:  # noqa: BLE001 - trace 故障不影响缓存
        log.debug("cache trace event skipped: %s", exc)


__all__ = ["record_cache_event"]
