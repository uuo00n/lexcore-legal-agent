"""Alembic 初始迁移可执行性测试。"""
from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_initial_migration_upgrade_and_downgrade(tmp_path, monkeypatch):
    db_path = tmp_path / "migration.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert {
            "alembic_version",
            "users",
            "conversations",
            "messages",
            "agent_runs",
            "tool_calls",
        } <= tables
    finally:
        engine.dispose()

    command.downgrade(config, "base")
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert tables == {"alembic_version"}
    finally:
        engine.dispose()
