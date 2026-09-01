"""LangGraph checkpoint 生命周期 + SQLite 上传文档元数据。

生产环境使用 PostgreSQL ``AsyncPostgresSaver``，开发和测试可通过
``CHECKPOINT_BACKEND=memory`` 使用进程内 ``MemorySaver``。
会话元数据已迁移至 PostgreSQL ``conversations`` 表。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Psycopg async 在 Windows 上不支持默认 ProactorEventLoop，必须在事件循环创建前
# 切换到 Selector policy。模块会在 FastAPI/pytest 建立 loop 前导入。
if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 限制 checkpoint 反序列化到 LangGraph 已知安全类型。显式环境配置仍可覆盖。
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from infrastructure.sanitize import mask_dsn


log = logging.getLogger(__name__)

CHECKPOINT_MEMORY = "memory"
CHECKPOINT_POSTGRES = "postgres"
CHECKPOINT_BACKENDS = (CHECKPOINT_MEMORY, CHECKPOINT_POSTGRES)


def selector_event_loop_factory() -> asyncio.AbstractEventLoop:
    """为 Uvicorn 创建 psycopg 异步连接兼容的事件循环。"""
    return asyncio.SelectorEventLoop()


def _ensure_postgres_event_loop_supported() -> None:
    """在 Windows Proactor loop 下给出可操作的启动提示。"""
    proactor_loop = getattr(asyncio, "ProactorEventLoop", None)
    if (
        sys.platform == "win32"
        and proactor_loop is not None
        and isinstance(asyncio.get_running_loop(), proactor_loop)
    ):
        raise RuntimeError(
            "PostgreSQL checkpoint requires a SelectorEventLoop on Windows; "
            "start Uvicorn with --loop services.checkpoint:selector_event_loop_factory"
        )


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def normalize_checkpoint_dsn(dsn: str) -> str:
    """把 SQLAlchemy/asyncpg URL 转为 psycopg 接受的 PostgreSQL conninfo URL。"""
    value = dsn.strip()
    for prefix in (
        "postgresql+asyncpg://",
        "postgresql+psycopg://",
        "postgresql+psycopg2://",
        "postgresql+pg8000://",
        "postgres://",
    ):
        if value.startswith(prefix):
            return "postgresql://" + value[len(prefix):]
    return value


@dataclass(frozen=True)
class CheckpointSettings:
    """LangGraph checkpoint 后端配置。"""

    backend: str = CHECKPOINT_MEMORY
    dsn: str | None = None
    auto_setup: bool = True
    pipeline: bool = False

    @property
    def safe_dsn(self) -> str:
        return mask_dsn(self.dsn) if self.dsn else "<memory>"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CheckpointSettings":
        env = os.environ if env is None else env
        backend = env.get("CHECKPOINT_BACKEND", CHECKPOINT_MEMORY).strip().lower()
        if backend not in CHECKPOINT_BACKENDS:
            allowed = ", ".join(CHECKPOINT_BACKENDS)
            raise ValueError(f"unsupported CHECKPOINT_BACKEND={backend!r}; expected {allowed}")

        dsn = None
        if backend == CHECKPOINT_POSTGRES:
            raw_dsn = (
                env.get("CHECKPOINT_DATABASE_URL")
                or env.get("DATABASE_URL")
                or env.get("POSTGRES_DSN")
                or ""
            ).strip()
            if not raw_dsn:
                from infrastructure.database import DatabaseSettings

                raw_dsn = DatabaseSettings.from_env(env).dsn
            dsn = normalize_checkpoint_dsn(raw_dsn)
            if not dsn.startswith(("postgresql://", "postgres://")):
                raise ValueError("PostgreSQL checkpoint requires a PostgreSQL connection URL")

        return cls(
            backend=backend,
            dsn=dsn,
            auto_setup=_env_bool(env, "CHECKPOINT_AUTO_SETUP", True),
            pipeline=_env_bool(env, "CHECKPOINT_PIPELINE", False),
        )


_checkpointer: BaseCheckpointSaver | None = None
_checkpoint_settings: CheckpointSettings | None = None
_meta_conn: Optional[sqlite3.Connection] = None


def _ensure_parent(path: str | Path) -> Path:
    """
    函数作用：
        待补充。
    输入参数：
        - path: str | Path
    输出参数：
        - Path
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def init_checkpointer(settings: CheckpointSettings | None = None) -> MemorySaver:
    """
    函数作用：
        初始化内存 checkpointer。PostgreSQL 后端必须使用 ``checkpoint_scope()``，
        以确保 psycopg 连接在整个图生命周期内保持打开。
    输入参数：
        - settings: CheckpointSettings | None
    输出参数：
        - MemorySaver
    """
    global _checkpointer, _checkpoint_settings
    resolved = settings or CheckpointSettings.from_env()
    if resolved.backend != CHECKPOINT_MEMORY:
        raise RuntimeError("postgres checkpoint must be initialized with checkpoint_scope()")
    _checkpointer = MemorySaver()
    _checkpoint_settings = resolved
    return _checkpointer


@asynccontextmanager
async def checkpoint_scope(
    settings: CheckpointSettings | None = None,
) -> AsyncIterator[BaseCheckpointSaver]:
    """创建并托管 checkpoint 后端，供 FastAPI lifespan 与集成测试使用。"""
    global _checkpointer, _checkpoint_settings
    resolved = settings or CheckpointSettings.from_env()
    _checkpoint_settings = resolved

    if resolved.backend == CHECKPOINT_MEMORY:
        saver: BaseCheckpointSaver = MemorySaver()
        _checkpointer = saver
        log.info("LangGraph checkpoint backend: memory")
        try:
            yield saver
        finally:
            _checkpointer = None
            _checkpoint_settings = None
        return

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:
        raise RuntimeError(
            "postgres checkpoint requires langgraph-checkpoint-postgres and psycopg[binary]"
        ) from exc

    _ensure_postgres_event_loop_supported()
    assert resolved.dsn is not None
    checkpoint_dsn = normalize_checkpoint_dsn(resolved.dsn)
    log.info("LangGraph checkpoint backend: postgres (%s)", mask_dsn(checkpoint_dsn))
    try:
        async with AsyncPostgresSaver.from_conn_string(
            checkpoint_dsn,
            pipeline=resolved.pipeline,
        ) as saver:
            if resolved.auto_setup:
                await saver.setup()
            _checkpointer = saver
            yield saver
    finally:
        _checkpointer = None
        _checkpoint_settings = None


def get_checkpointer() -> BaseCheckpointSaver:
    """返回当前 lifespan 管理的 checkpointer。"""
    if _checkpointer is None:
        raise RuntimeError("checkpointer not initialized; enter checkpoint_scope() first")
    return _checkpointer


def get_checkpoint_settings() -> CheckpointSettings:
    """返回当前 checkpoint 配置。"""
    if _checkpoint_settings is None:
        raise RuntimeError("checkpointer not initialized")
    return _checkpoint_settings


def init_meta_db(db_path: str | None = None) -> sqlite3.Connection:
    """
    函数作用：
        初始化辅助元数据库（仅上传文档及其他未迁移模块）。
    输入参数：
        - db_path: str | None，默认值 None
    输出参数：
        - sqlite3.Connection
    """
    global _meta_conn
    db_path = db_path or os.getenv("DOCS_DB", "data/docs.sqlite")
    _ensure_parent(db_path)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS docs (
            doc_id     TEXT PRIMARY KEY,
            filename   TEXT NOT NULL,
            text       TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            truncated  INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    _meta_conn = conn
    return conn


def get_meta_conn() -> sqlite3.Connection:
    """
    函数作用：
        待补充。
    输入参数：
        - 无
    输出参数：
        - sqlite3.Connection
    """
    if _meta_conn is None:
        raise RuntimeError("meta db not initialized; call init_meta_db() first")
    return _meta_conn


def save_doc(doc_id: str, filename: str, text: str, truncated: bool) -> None:
    """
    函数作用：
        待补充。
    输入参数：
        - doc_id: str
        - filename: str
        - text: str
        - truncated: bool
    输出参数：
        - 无
    """
    conn = get_meta_conn()
    conn.execute(
        "INSERT INTO docs (doc_id, filename, text, char_count, truncated, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, filename, text, len(text), 1 if truncated else 0, int(time.time())),
    )
    conn.commit()


def load_doc(doc_id: str) -> Optional[dict]:
    """
    函数作用：
        待补充。
    输入参数：
        - doc_id: str
    输出参数：
        - Optional[dict]
    """
    conn = get_meta_conn()
    cur = conn.execute(
        "SELECT doc_id, filename, text, char_count, truncated FROM docs WHERE doc_id = ?",
        (doc_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "doc_id": row[0],
        "filename": row[1],
        "text": row[2],
        "char_count": row[3],
        "truncated": bool(row[4]),
    }


def reset_for_tests() -> None:
    """
    函数作用：
        仅供测试使用，清空全局单例。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    global _checkpointer, _checkpoint_settings, _meta_conn
    if _meta_conn is not None:
        _meta_conn.close()
    _checkpointer = None
    _checkpoint_settings = None
    _meta_conn = None


__all__ = [
    "CHECKPOINT_BACKENDS",
    "CHECKPOINT_MEMORY",
    "CHECKPOINT_POSTGRES",
    "CheckpointSettings",
    "checkpoint_scope",
    "get_checkpoint_settings",
    "get_checkpointer",
    "get_meta_conn",
    "init_checkpointer",
    "init_meta_db",
    "load_doc",
    "normalize_checkpoint_dsn",
    "reset_for_tests",
    "save_doc",
    "selector_event_loop_factory",
]
