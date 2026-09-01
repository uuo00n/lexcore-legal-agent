"""Alembic 环境入口。

要点：
- 连接串只从环境变量读取（`DatabaseSettings.from_env()`），日志一律输出脱敏 DSN。
- 在线模式走 asyncpg：先建 AsyncEngine，再用 `connection.run_sync` 执行同步的
  Alembic 迁移逻辑。
- 离线模式（`--sql`）需要同步驱动串，因此用 `to_sync_dsn()` 转换。
- `target_metadata` 取 `infrastructure.models` 注册后的 `Base.metadata`，
  因此新增模型必须在 `infrastructure/models/__init__.py` 导出，否则 autogenerate 会漏表。
"""
from __future__ import annotations

import asyncio
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

import infrastructure.models  # noqa: F401  导入即注册全部模型
from infrastructure.database import DatabaseSettings, build_engine, to_sync_dsn
from infrastructure.models.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

log = logging.getLogger("alembic.env")

target_metadata = Base.metadata

_settings = DatabaseSettings.from_env()


def run_migrations_offline() -> None:
    """
    函数作用：
        离线模式：不建立连接，直接把 DDL 输出为 SQL 脚本。
    输入参数：
        - 无
    输出参数：
        - None
    """
    log.info("Alembic 离线迁移: %s", _settings.safe_dsn)
    context.configure(
        url=to_sync_dsn(_settings.dsn),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """
    函数作用：
        在同步连接上执行迁移，由 `run_sync` 调用。
    输入参数：
        - connection: Connection
    输出参数：
        - None
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """
    函数作用：
        在线模式：建 AsyncEngine 并在其上执行迁移。
    输入参数：
        - 无
    输出参数：
        - None
    """
    log.info("Alembic 在线迁移: %s", _settings.safe_dsn)
    engine = build_engine(_settings)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
