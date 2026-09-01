"""缓存包：统一收口所有缓存与限流实现。

各子模块职责：
- `response`  ：问答响应缓存（SQLite，主图前的短路分支）
- `retrieval` ：Hybrid RAG 检索结果缓存（Redis，同步）
- `delilegal` ：得理 API 响应缓存（Redis，异步）
- `rate_limit`：固定窗口限流（Redis，fail-open）
- `session`   ：会话元数据热层（Redis，仅非内容字段）
- `idempotency`：幂等标记（Redis，SET NX EX）
- `keys`      ：key 构造与内容摘要（唯一入口，保证 key 不含敏感数据）
- `trace`     ：cache_hit / cache_miss 的 trace 记录与指标打点

顶层导出响应缓存 API，保持与重构前 `from services.cache import get_cached_answer, ...`
的调用方式兼容。子模块与响应缓存都通过模块级 `__getattr__` 惰性加载：
`response` 依赖 `services.checkpoint`（连带 langgraph），而 MCP 子进程里的同步检索
链路只需要 `retrieval`，不应为此付整条 checkpoint 依赖的导入开销。

Redis 相关模块各自有同名函数（`cache_enabled` / `build_key` / `ttl_seconds`），
因此按模块使用，例如 `from services.cache import retrieval as retrieval_cache`。
"""
from __future__ import annotations

import importlib
from typing import Any

from services.cache.keys import digest, fingerprint, normalize_text
from services.cache.trace import record_cache_event

# 从 services.cache.response 转发的名字，保持旧调用方式可用。
_RESPONSE_EXPORTS = frozenset(
    {
        "cache_enabled",
        "get_cached_answer",
        "init_cache_tables",
        "make_cache_key",
        "set_cached_answer",
    }
)

_SUBMODULES = frozenset(
    {
        "delilegal",
        "idempotency",
        "keys",
        "rate_limit",
        "response",
        "retrieval",
        "session",
        "trace",
    }
)


def __getattr__(name: str) -> Any:
    """
    函数作用：
        惰性解析子模块与响应缓存导出名（PEP 562），避免导入本包就拉起
        langgraph checkpoint 依赖。
    输入参数：
        - name: str，被访问的属性名
    输出参数：
        - Any
    """
    if name in _SUBMODULES:
        module = importlib.import_module(f"services.cache.{name}")
        globals()[name] = module
        return module
    if name in _RESPONSE_EXPORTS:
        value = getattr(importlib.import_module("services.cache.response"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """
    函数作用：
        让 dir() 与自动补全能看到惰性导出的名字。
    输入参数：
        - 无
    输出参数：
        - list[str]
    """
    return sorted(set(globals()) | _SUBMODULES | _RESPONSE_EXPORTS)


__all__ = [
    "cache_enabled",
    "delilegal",
    "digest",
    "fingerprint",
    "get_cached_answer",
    "idempotency",
    "init_cache_tables",
    "keys",
    "make_cache_key",
    "normalize_text",
    "rate_limit",
    "record_cache_event",
    "response",
    "retrieval",
    "session",
    "set_cached_answer",
    "trace",
]
