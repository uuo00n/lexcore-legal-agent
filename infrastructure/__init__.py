"""持久化与缓存基础设施。

分层：
- `database`：PostgreSQL 连接配置、引擎、会话工厂与生命周期。
- `models`：SQLAlchemy 2 ORM 模型（全部带 created_at / updated_at）。
- `repositories`：唯一的写入入口，所有 JSONB 与 error 字段在此脱敏。
- `migrations`：Alembic 迁移脚本。
- `sanitize`：脱敏工具，保证 API Key / 凭据不会落库。
- `redis`：缓存 / 限流 / 会话元数据 / 幂等的连接与降级入口。
  与 `database` 存在同名函数（`get_settings` / `ping` / `init_*`），
  因此不在本包顶层再导出，请直接 `from infrastructure.redis import ...`。
"""
from __future__ import annotations

from infrastructure.database import (
    DatabaseSettings,
    create_schema,
    dispose_database,
    drop_schema,
    get_engine,
    get_session,
    get_session_factory,
    get_settings,
    init_database,
    is_database_initialized,
    ping,
    session_scope,
)
from infrastructure.sanitize import mask_dsn, redact, redact_mapping, redact_text

__all__ = [
    "DatabaseSettings",
    "create_schema",
    "dispose_database",
    "drop_schema",
    "get_engine",
    "get_session",
    "get_session_factory",
    "get_settings",
    "init_database",
    "is_database_initialized",
    "mask_dsn",
    "ping",
    "redact",
    "redact_mapping",
    "redact_text",
    "session_scope",
]
