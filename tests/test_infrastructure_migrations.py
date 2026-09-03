"""PostgreSQL Alembic 迁移链测试。"""
from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from infrastructure.operational_store import REQUIRED_TABLES


def test_migration_chain_has_operational_storage_revision():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = list(script.walk_revisions(base="base", head="heads"))

    assert [revision.revision for revision in revisions] == [
        "0002_operational",
        "0001_initial",
    ]
    assert revisions[0].down_revision == "0001_initial"


def test_operational_migration_defines_every_required_table():
    migration = Path("infrastructure/migrations/versions/0002_operational_storage.py").read_text(
        encoding="utf-8"
    )

    for table_name in REQUIRED_TABLES:
        assert f'"{table_name}"' in migration
