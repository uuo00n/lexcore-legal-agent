"""PostgreSQL + Redis + Qdrant 单一路径架构门禁。"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS = (
    ROOT / "main.py",
    ROOT / "agent",
    ROOT / "api",
    ROOT / "eval",
    ROOT / "infrastructure",
    ROOT / "mcp_server",
    ROOT / "services",
)


def _runtime_python_files():
    for root in RUNTIME_ROOTS:
        if root.is_file():
            yield root
        else:
            yield from root.rglob("*.py")


def test_runtime_has_no_embedded_database_or_removed_vector_backend():
    forbidden = (
        "sql" + "ite",
        "chroma" + "db",
        "chroma_store",
        "DOCS" + "_DB",
        "CHROMA" + "_DB_PATH",
    )
    violations = []
    for path in _runtime_python_files():
        content = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            if token.lower() in content:
                violations.append(f"{path.relative_to(ROOT)}: {token}")

    assert violations == []


def test_runtime_configuration_selects_only_qdrant():
    rag_factory = (ROOT / "services" / "rag" / "__init__.py").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'os.getenv("VECTOR_STORE", "qdrant")' in rag_factory
    assert "VECTOR_STORE: qdrant" in compose
    assert "QDRANT_MEMORY_COLLECTION:" in compose


def test_orm_package_is_deliverable_not_ignored_as_model_weights():
    """`models/` 曾同时匹配根目录权重和 infrastructure/models/ ORM 源码。

    被忽略时干净检出会缺整个 ORM 包，main.py 与 Alembic 直接 ImportError，
    因此规则必须锚定到仓库根目录。
    """
    orm_package = ROOT / "infrastructure" / "models"
    assert (orm_package / "__init__.py").is_file()
    assert {path.name for path in orm_package.glob("*.py")} >= {
        "agent_run.py",
        "base.py",
        "conversation.py",
        "message.py",
        "operational.py",
        "tool_call.py",
        "user.py",
    }

    rules = [
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "/models/" in rules
    assert "models/" not in rules
    assert "models" not in rules
