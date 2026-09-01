"""PostgreSQL 异步数据访问基础设施。

职责：
- 从环境变量解析连接配置，并把同步 DSN 规范化为 asyncpg 驱动形式。
- 维护进程内唯一的 `AsyncEngine` 与 `async_sessionmaker`。
- 提供 `session_scope()`（脚本 / 后台任务）和 `get_session()`（FastAPI 依赖）两种会话入口。
- 提供健康检查与测试建表工具。

连接串包含数据库密码，因此所有日志输出一律使用 `mask_dsn()` 处理后的形式。
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from infrastructure.models.base import Base
from infrastructure.sanitize import mask_dsn
from services.errors import DatabaseError
from services.retry import is_retryable_exception

log = logging.getLogger(__name__)

# 默认本地开发连接，生产必须通过环境变量覆盖。
DEFAULT_DSN = "postgresql+asyncpg://legal:legal@localhost:5432/legal"

# 同步驱动 -> asyncpg 的规范化映射。
_SYNC_SCHEME_PREFIXES = (
    "postgresql+psycopg2://",
    "postgresql+psycopg://",
    "postgresql+pg8000://",
    "postgresql://",
    "postgres://",
)

# asyncpg 不接受 libpq 风格的这些查询参数，需要在建引擎前剥离。
_UNSUPPORTED_QUERY_KEYS = ("sslmode", "target_session_attrs", "options", "channel_binding")


def normalize_dsn(dsn: str) -> str:
    """
    函数作用：
        把任意 PostgreSQL DSN 规范化为 asyncpg 形式，并剥离 asyncpg 不支持的查询参数。
    输入参数：
        - dsn: str
    输出参数：
        - str
    """
    normalized = dsn.strip()
    for prefix in _SYNC_SCHEME_PREFIXES:
        if normalized.startswith(prefix):
            normalized = "postgresql+asyncpg://" + normalized[len(prefix):]
            break
    if "?" not in normalized:
        return normalized
    base, _, query = normalized.partition("?")
    kept = [
        item
        for item in query.split("&")
        if item and item.split("=", 1)[0].lower() not in _UNSUPPORTED_QUERY_KEYS
    ]
    return f"{base}?{'&'.join(kept)}" if kept else base


def to_sync_dsn(dsn: str) -> str:
    """
    函数作用：
        把 asyncpg DSN 转回同步驱动形式，供 Alembic 离线模式或运维脚本使用。
    输入参数：
        - dsn: str
    输出参数：
        - str
    """
    return dsn.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)


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
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DatabaseSettings:
    """数据库连接与连接池配置。"""

    dsn: str
    echo: bool = False
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout: int = 30
    pool_recycle: int = 1800
    statement_timeout_ms: int = 0

    @property
    def is_postgres(self) -> bool:
        """
        函数作用：
            判断当前 DSN 是否为 PostgreSQL，用于决定是否传连接池与 asyncpg 专属参数。
        输入参数：
            - 无
        输出参数：
            - bool
        """
        return self.dsn.startswith("postgresql")

    @property
    def safe_dsn(self) -> str:
        """
        函数作用：
            返回隐藏密码后的 DSN，可安全写入日志与健康检查响应。
        输入参数：
            - 无
        输出参数：
            - str
        """
        return mask_dsn(self.dsn)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DatabaseSettings":
        """
        函数作用：
            从环境变量构造配置。优先 DATABASE_URL / POSTGRES_DSN，
            否则用 POSTGRES_HOST/PORT/USER/PASSWORD/DB 拼装。
        输入参数：
            - env: Mapping[str, str] | None，默认值 None 表示使用 os.environ
        输出参数：
            - DatabaseSettings
        """
        env = os.environ if env is None else env
        raw_dsn = (env.get("DATABASE_URL") or env.get("POSTGRES_DSN") or "").strip()
        if not raw_dsn:
            host = env.get("POSTGRES_HOST", "").strip()
            if host:
                user = quote_plus(env.get("POSTGRES_USER", "legal"))
                password = quote_plus(env.get("POSTGRES_PASSWORD", ""))
                port = _env_int(env, "POSTGRES_PORT", 5432)
                database = env.get("POSTGRES_DB", "legal")
                credentials = f"{user}:{password}" if password else user
                raw_dsn = f"postgresql+asyncpg://{credentials}@{host}:{port}/{database}"
            else:
                raw_dsn = DEFAULT_DSN
        return cls(
            dsn=normalize_dsn(raw_dsn),
            echo=_env_bool(env, "POSTGRES_ECHO"),
            pool_size=_env_int(env, "POSTGRES_POOL_SIZE", 10),
            max_overflow=_env_int(env, "POSTGRES_MAX_OVERFLOW", 20),
            pool_timeout=_env_int(env, "POSTGRES_POOL_TIMEOUT", 30),
            pool_recycle=_env_int(env, "POSTGRES_POOL_RECYCLE", 1800),
            statement_timeout_ms=_env_int(env, "POSTGRES_STATEMENT_TIMEOUT_MS", 0),
        )


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_settings: DatabaseSettings | None = None


def is_database_initialized() -> bool:
    """返回 PostgreSQL 基础设施是否已经完成进程内初始化。"""
    return _engine is not None and _session_factory is not None


def build_engine(settings: DatabaseSettings) -> AsyncEngine:
    """
    函数作用：
        按配置创建 AsyncEngine。连接池与 asyncpg 专属参数只在 PostgreSQL 上传递，
        以便测试可以直接换成 sqlite+aiosqlite。
    输入参数：
        - settings: DatabaseSettings
    输出参数：
        - AsyncEngine
    """
    kwargs: dict[str, object] = {"echo": settings.echo}
    if settings.is_postgres:
        kwargs.update(
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            pool_timeout=settings.pool_timeout,
            pool_recycle=settings.pool_recycle,
            pool_pre_ping=True,
        )
        if settings.statement_timeout_ms > 0:
            kwargs["connect_args"] = {
                "server_settings": {"statement_timeout": str(settings.statement_timeout_ms)}
            }
    elif ":memory:" in settings.dsn:
        # 内存 SQLite 必须共用同一条连接，否则每次 checkout 都是一个空库。
        kwargs["poolclass"] = StaticPool
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_async_engine(settings.dsn, **kwargs)


def init_database(settings: DatabaseSettings | None = None) -> AsyncEngine:
    """
    函数作用：
        初始化进程内数据库引擎与会话工厂，可重复调用（重复调用返回已有引擎）。
    输入参数：
        - settings: DatabaseSettings | None，默认值 None 表示从环境变量读取
    输出参数：
        - AsyncEngine
    """
    global _engine, _session_factory, _settings
    if _engine is not None:
        return _engine
    resolved = settings or DatabaseSettings.from_env()
    _settings = resolved
    _engine = build_engine(resolved)
    _session_factory = async_sessionmaker(
        bind=_engine,
        expire_on_commit=False,
        autoflush=False,
    )
    log.info("数据库引擎已初始化: %s", resolved.safe_dsn)
    return _engine


def get_engine() -> AsyncEngine:
    """
    函数作用：
        返回已初始化的引擎。
    输入参数：
        - 无
    输出参数：
        - AsyncEngine
    """
    if _engine is None:
        raise RuntimeError("database not initialized; call init_database() first")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    函数作用：
        返回已初始化的会话工厂。
    输入参数：
        - 无
    输出参数：
        - async_sessionmaker[AsyncSession]
    """
    if _session_factory is None:
        raise RuntimeError("database not initialized; call init_database() first")
    return _session_factory


def get_settings() -> DatabaseSettings:
    """
    函数作用：
        返回当前生效的数据库配置。
    输入参数：
        - 无
    输出参数：
        - DatabaseSettings
    """
    if _settings is None:
        raise RuntimeError("database not initialized; call init_database() first")
    return _settings


async def dispose_database() -> None:
    """
    函数作用：
        释放连接池并清空进程内引擎/会话工厂，供 FastAPI shutdown 与测试收尾调用。
    输入参数：
        - 无
    输出参数：
        - None
    """
    global _engine, _session_factory, _settings
    if _engine is not None:
        await _engine.dispose()
        log.info("数据库引擎已释放")
    _engine = None
    _session_factory = None
    _settings = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """
    函数作用：
        提供带事务边界的会话上下文：正常退出提交，异常回滚，最后关闭。
        适用于脚本、后台任务和 Graph 节点。
    输入参数：
        - 无
    输出参数：
        - AsyncIterator[AsyncSession]
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        await session.commit()
    except SQLAlchemyError as exc:
        try:
            await session.rollback()
        except SQLAlchemyError:
            log.exception("数据库事务回滚失败")
        raise DatabaseError(
            "Database transaction failed.",
            retryable=is_retryable_exception(exc),
        ) from exc
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    """
    函数作用：
        FastAPI 依赖入口，语义与 session_scope() 一致。
    输入参数：
        - 无
    输出参数：
        - AsyncIterator[AsyncSession]
    """
    async with session_scope() as session:
        yield session


async def ping() -> bool:
    """
    函数作用：
        执行 SELECT 1 探活，供 /api/health 与运维脚本使用。失败只记录脱敏后的 DSN。
    输入参数：
        - 无
    输出参数：
        - bool，True 表示连接可用
    """
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 - 健康检查不应向上抛出
        safe = _settings.safe_dsn if _settings else "<uninitialized>"
        log.warning("数据库探活失败: dsn=%s error=%s", safe, type(exc).__name__)
        return False


async def create_schema() -> None:
    """
    函数作用：
        直接按 ORM 元数据建表。仅供测试与本地快速起步使用，
        生产环境必须走 Alembic migrations。
    输入参数：
        - 无
    输出参数：
        - None
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_schema() -> None:
    """
    函数作用：
        按 ORM 元数据删表，仅供测试收尾使用。
    输入参数：
        - 无
    输出参数：
        - None
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


__all__ = [
    "DEFAULT_DSN",
    "DatabaseSettings",
    "build_engine",
    "create_schema",
    "dispose_database",
    "drop_schema",
    "get_engine",
    "get_session",
    "get_session_factory",
    "get_settings",
    "init_database",
    "is_database_initialized",
    "normalize_dsn",
    "ping",
    "session_scope",
    "to_sync_dsn",
]
