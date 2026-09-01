"""Redis 缓存 / 限流 / 会话元数据基础设施。

职责：
- 从环境变量解析连接配置，维护进程内唯一的异步与同步 Redis 客户端。
- 提供统一降级执行入口 `execute()` / `execute_sync()`：Redis 不可用时返回默认值而不上抛，
  因此 Agent 主链在 Redis 挂掉时仍然可以运行。
- 用熔断器避免 Redis 宕机后每个请求都付一次连接超时。
- 提供带前缀的 key 构造、探活和状态查询。

Redis 在本项目中只承担缓存、限流、会话元数据和幂等标记，不是任何数据的唯一副本，
也不是长期 Memory 主数据库；所有失败一律降级。连接串可能带密码，日志统一用 `safe_url`。

同步客户端只为 MCP 子进程内的同步检索链路存在（`services/rag/retriever.py` 全链路同步），
其余调用方一律使用异步客户端。
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from infrastructure.sanitize import mask_dsn

log = logging.getLogger(__name__)

T = TypeVar("T")

# 默认本地开发连接，生产必须通过 REDIS_URL 覆盖。
DEFAULT_REDIS_URL = "redis://localhost:6379/0"

# 所有 key 的默认前缀，便于与同实例上的其他应用隔离。
DEFAULT_KEY_PREFIX = "legal"

# Redis 故障后熔断器保持打开的秒数，期间直接降级不再尝试连接。
DEFAULT_BREAKER_COOLDOWN = 30.0


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    """
    函数作用：
        读取整数环境变量，非法值回退默认并告警。
    输入参数：
        - env: Mapping[str, str]
        - key: str
        - default: int
    输出参数：
        - int
    """
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("环境变量 %s=%r 不是整数，回退默认值 %s", key, raw, default)
        return default


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    """
    函数作用：
        读取浮点环境变量，非法值回退默认并告警。
    输入参数：
        - env: Mapping[str, str]
        - key: str
        - default: float
    输出参数：
        - float
    """
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("环境变量 %s=%r 不是数值，回退默认值 %s", key, raw, default)
        return default


def _env_bool(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    """
    函数作用：
        读取布尔环境变量。
    输入参数：
        - env: Mapping[str, str]
        - key: str
        - default: bool，默认值 False
    输出参数：
        - bool
    """
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RedisSettings:
    """Redis 连接与降级行为配置；实例本身不会输出密码。"""

    url: str = DEFAULT_REDIS_URL
    enabled: bool = True
    key_prefix: str = DEFAULT_KEY_PREFIX
    socket_timeout: float = 1.0
    connect_timeout: float = 1.0
    max_connections: int = 20
    health_check_interval: int = 30
    breaker_cooldown: float = DEFAULT_BREAKER_COOLDOWN

    @property
    def safe_url(self) -> str:
        """
        函数作用：
            返回隐藏密码后的连接串，可安全写入日志与健康检查响应。
        输入参数：
            - 无
        输出参数：
            - str
        """
        return mask_dsn(self.url)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RedisSettings":
        """
        函数作用：
            从环境变量构造配置。REDIS_URL 为空时视为未启用 Redis，
            全部缓存/限流入口直接降级。
        输入参数：
            - env: Mapping[str, str] | None，默认值 None 表示使用 os.environ
        输出参数：
            - RedisSettings
        """
        env = os.environ if env is None else env
        url = (env.get("REDIS_URL") or "").strip()
        prefix = (env.get("REDIS_KEY_PREFIX") or DEFAULT_KEY_PREFIX).strip() or DEFAULT_KEY_PREFIX
        return cls(
            url=url or DEFAULT_REDIS_URL,
            enabled=_env_bool(env, "REDIS_ENABLED", bool(url)),
            key_prefix=prefix,
            socket_timeout=_env_float(env, "REDIS_SOCKET_TIMEOUT", 1.0),
            connect_timeout=_env_float(env, "REDIS_CONNECT_TIMEOUT", 1.0),
            max_connections=_env_int(env, "REDIS_MAX_CONNECTIONS", 20),
            health_check_interval=_env_int(env, "REDIS_HEALTH_CHECK_INTERVAL", 30),
            breaker_cooldown=_env_float(
                env, "REDIS_BREAKER_COOLDOWN_SECONDS", DEFAULT_BREAKER_COOLDOWN
            ),
        )


_settings: RedisSettings | None = None
_client: Any | None = None
_sync_client: Any | None = None
_breaker_open_until: float = 0.0
_last_error: str = ""
_import_failed: bool = False


def _redis_error_types() -> tuple[type[BaseException], ...]:
    """
    函数作用：
        懒构造需要降级处理的异常元组。redis 包缺失时只保留内建网络异常，
        使调用方在没装 redis 的环境里也能安全 import 本模块。
    输入参数：
        - 无
    输出参数：
        - tuple[type[BaseException], ...]
    """
    base: tuple[type[BaseException], ...] = (OSError, TimeoutError, ValueError)
    try:
        from redis.exceptions import RedisError
    except Exception:  # noqa: BLE001 - redis 未安装时按内建异常处理
        return base
    return base + (RedisError,)


def _build_async_client(settings: RedisSettings) -> Any | None:
    """
    函数作用：
        创建异步 Redis 客户端；redis 包缺失或参数非法时返回 None 并降级。
    输入参数：
        - settings: RedisSettings
    输出参数：
        - Any | None
    """
    global _import_failed
    try:
        from redis.asyncio import Redis
    except Exception as exc:  # noqa: BLE001 - 缺依赖不应阻塞应用启动
        _import_failed = True
        log.warning("redis 包不可用，Redis 缓存与限流已禁用: %s", type(exc).__name__)
        return None
    try:
        return Redis.from_url(
            settings.url,
            decode_responses=True,
            socket_timeout=settings.socket_timeout,
            socket_connect_timeout=settings.connect_timeout,
            max_connections=settings.max_connections,
            health_check_interval=settings.health_check_interval,
        )
    except Exception as exc:  # noqa: BLE001 - 非法 URL 同样降级
        log.warning("Redis 客户端创建失败: url=%s error=%s", settings.safe_url, type(exc).__name__)
        return None


def _build_sync_client(settings: RedisSettings) -> Any | None:
    """
    函数作用：
        创建同步 Redis 客户端，供 MCP 子进程内的同步检索链路使用。
    输入参数：
        - settings: RedisSettings
    输出参数：
        - Any | None
    """
    try:
        from redis import Redis
    except Exception:  # noqa: BLE001 - 与异步路径一致地降级
        return None
    try:
        return Redis.from_url(
            settings.url,
            decode_responses=True,
            socket_timeout=settings.socket_timeout,
            socket_connect_timeout=settings.connect_timeout,
            max_connections=settings.max_connections,
            health_check_interval=settings.health_check_interval,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("同步 Redis 客户端创建失败: error=%s", type(exc).__name__)
        return None


def init_redis(
    settings: RedisSettings | None = None,
    *,
    client: Any | None = None,
    sync_client: Any | None = None,
) -> Any | None:
    """
    函数作用：
        初始化进程内 Redis 客户端，可重复调用（已初始化时直接返回现有客户端）。
        `client` / `sync_client` 供测试注入替身，注入后不会创建真实连接。
    输入参数：
        - settings: RedisSettings | None，默认值 None 表示从环境变量读取
        - client: Any | None，默认值 None，注入的异步客户端
        - sync_client: Any | None，默认值 None，注入的同步客户端
    输出参数：
        - Any | None，未启用或依赖缺失时返回 None
    """
    global _settings, _client, _sync_client, _breaker_open_until, _last_error
    if _client is not None or _sync_client is not None:
        return _client
    resolved = settings or RedisSettings.from_env()
    _settings = resolved
    _breaker_open_until = 0.0
    _last_error = ""
    if client is not None or sync_client is not None:
        _client = client
        _sync_client = sync_client
        return _client
    if not resolved.enabled:
        log.info("Redis 未启用（REDIS_URL 未配置或 REDIS_ENABLED=false），缓存与限流降级运行")
        return None
    _client = _build_async_client(resolved)
    if _client is not None:
        log.info("Redis 客户端已初始化: %s", resolved.safe_url)
    return _client


def get_settings() -> RedisSettings:
    """
    函数作用：
        返回当前生效的 Redis 配置；未初始化时按环境变量惰性解析。
    输入参数：
        - 无
    输出参数：
        - RedisSettings
    """
    global _settings
    if _settings is None:
        _settings = RedisSettings.from_env()
    return _settings


def redis_enabled() -> bool:
    """
    函数作用：
        返回配置层面是否启用 Redis。
    输入参数：
        - 无
    输出参数：
        - bool
    """
    return get_settings().enabled and not _import_failed


def breaker_open() -> bool:
    """
    函数作用：
        返回熔断器当前是否打开。打开期间所有 Redis 调用直接降级。
    输入参数：
        - 无
    输出参数：
        - bool
    """
    return _breaker_open_until > time.monotonic()


def _open_breaker(op: str, exc: BaseException) -> None:
    """
    函数作用：
        记录失败并打开熔断器，避免 Redis 宕机后每个请求都付一次连接超时。
    输入参数：
        - op: str，失败的操作名
        - exc: BaseException
    输出参数：
        - 无
    """
    global _breaker_open_until, _last_error
    settings = get_settings()
    _breaker_open_until = time.monotonic() + max(settings.breaker_cooldown, 0.0)
    _last_error = type(exc).__name__
    log.warning(
        "Redis 操作失败，降级并熔断 %.0fs: op=%s url=%s error=%s",
        settings.breaker_cooldown,
        op,
        settings.safe_url,
        _last_error,
    )


def _close_breaker() -> None:
    """
    函数作用：
        操作成功后关闭熔断器。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    global _breaker_open_until, _last_error
    if _breaker_open_until:
        log.info("Redis 已恢复，熔断器关闭")
    _breaker_open_until = 0.0
    _last_error = ""


def _record_degraded(op: str) -> None:
    """
    函数作用：
        为降级调用打点，便于在 /metrics 观察 Redis 不可用的影响面。
    输入参数：
        - op: str
    输出参数：
        - 无
    """
    try:
        from services.metrics import inc_counter

        inc_counter("legal_redis_degraded_total", {"op": op})
    except Exception:  # noqa: BLE001 - 打点失败不得影响主链路
        pass


def get_redis() -> Any | None:
    """
    函数作用：
        返回可用的异步 Redis 客户端；未启用、依赖缺失或熔断打开时返回 None。
        调用方拿到 None 必须走降级分支，而不是抛错。
    输入参数：
        - 无
    输出参数：
        - Any | None
    """
    if _client is None or breaker_open():
        return None
    return _client


def get_sync_redis() -> Any | None:
    """
    函数作用：
        返回可用的同步 Redis 客户端；仅供 MCP 子进程内的同步检索链路使用。
        首次调用时惰性创建，失败后与异步路径共用同一个熔断器。
    输入参数：
        - 无
    输出参数：
        - Any | None
    """
    global _sync_client
    if breaker_open() or not redis_enabled():
        return None
    if _sync_client is None:
        _sync_client = _build_sync_client(get_settings())
    return _sync_client


async def execute(
    op: str,
    action: Callable[[Any], Awaitable[T]],
    *,
    default: T | None = None,
) -> T | None:
    """
    函数作用：
        Redis 异步操作的唯一降级入口。客户端不可用、熔断打开或操作失败时，
        返回 `default` 并打点，绝不向调用方抛出异常。
    输入参数：
        - op: str，操作名，用于日志与指标标签
        - action: Callable[[Any], Awaitable[T]]，接收客户端并返回协程
        - default: T | None，默认值 None，降级时的返回值
    输出参数：
        - T | None
    """
    client = get_redis()
    if client is None:
        _record_degraded(op)
        return default
    try:
        result = await action(client)
    except _redis_error_types() as exc:
        _open_breaker(op, exc)
        _record_degraded(op)
        return default
    except Exception as exc:  # noqa: BLE001 - 缓存不得成为故障源
        _open_breaker(op, exc)
        _record_degraded(op)
        return default
    _close_breaker()
    return result


def execute_sync(
    op: str,
    action: Callable[[Any], T],
    *,
    default: T | None = None,
) -> T | None:
    """
    函数作用：
        `execute()` 的同步版本，语义完全一致，供同步检索链路使用。
    输入参数：
        - op: str
        - action: Callable[[Any], T]
        - default: T | None，默认值 None
    输出参数：
        - T | None
    """
    client = get_sync_redis()
    if client is None:
        _record_degraded(op)
        return default
    try:
        result = action(client)
    except _redis_error_types() as exc:
        _open_breaker(op, exc)
        _record_degraded(op)
        return default
    except Exception as exc:  # noqa: BLE001
        _open_breaker(op, exc)
        _record_degraded(op)
        return default
    _close_breaker()
    return result


def make_key(namespace: str, *parts: str) -> str:
    """
    函数作用：
        构造带全局前缀的 Redis key：`{prefix}:{namespace}:{part}:{part}`。
        调用方负责保证 parts 内不含原始敏感内容（应先哈希）。
    输入参数：
        - namespace: str
        - parts: str，可变参数
    输出参数：
        - str
    """
    segments = [get_settings().key_prefix, namespace, *[str(part) for part in parts if part != ""]]
    return ":".join(segments)


async def ping() -> bool:
    """
    函数作用：
        探活，供 /api/health 与运维脚本使用。失败只记录脱敏 URL。
    输入参数：
        - 无
    输出参数：
        - bool，True 表示 Redis 可用
    """
    result = await execute("ping", lambda client: client.ping(), default=False)
    return bool(result)


def redis_status() -> dict[str, Any]:
    """
    函数作用：
        返回 Redis 当前状态摘要，可安全放进健康检查响应（不含密码）。
    输入参数：
        - 无
    输出参数：
        - dict[str, Any]
    """
    settings = get_settings()
    if not settings.enabled or _import_failed:
        state = "disabled"
    elif _client is None and _sync_client is None:
        state = "uninitialized"
    elif breaker_open():
        state = "degraded"
    else:
        state = "ok"
    return {
        "state": state,
        "url": settings.safe_url if settings.enabled else "",
        "last_error": _last_error,
    }


async def dispose_redis() -> None:
    """
    函数作用：
        关闭连接池并清空进程内客户端，供 FastAPI shutdown 与测试收尾调用。
    输入参数：
        - 无
    输出参数：
        - None
    """
    global _client, _sync_client, _settings, _breaker_open_until, _last_error
    if _client is not None:
        closer = getattr(_client, "aclose", None) or getattr(_client, "close", None)
        if closer is not None:
            try:
                result = closer()
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:  # noqa: BLE001 - 关闭失败只记录
                log.warning("Redis 客户端关闭失败: %s", type(exc).__name__)
        log.info("Redis 客户端已释放")
    if _sync_client is not None:
        try:
            _sync_client.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("同步 Redis 客户端关闭失败: %s", type(exc).__name__)
    _client = None
    _sync_client = None
    _settings = None
    _breaker_open_until = 0.0
    _last_error = ""


def reset_for_tests() -> None:
    """
    函数作用：
        同步清空进程内客户端与熔断状态，供测试 setup/teardown 使用。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    global _client, _sync_client, _settings, _breaker_open_until, _last_error, _import_failed
    _client = None
    _sync_client = None
    _settings = None
    _breaker_open_until = 0.0
    _last_error = ""
    _import_failed = False


__all__ = [
    "DEFAULT_KEY_PREFIX",
    "DEFAULT_REDIS_URL",
    "RedisSettings",
    "breaker_open",
    "dispose_redis",
    "execute",
    "execute_sync",
    "get_redis",
    "get_settings",
    "get_sync_redis",
    "init_redis",
    "make_key",
    "ping",
    "redis_enabled",
    "redis_status",
    "reset_for_tests",
]
