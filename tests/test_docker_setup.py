"""Docker Compose 启动拓扑的静态回归测试。"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compose_wires_all_required_services() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    for service in ("postgres:", "redis:", "qdrant:", "migrate:", "app:"):
        assert service in compose

    assert "POSTGRES_HOST: postgres" in compose
    assert "REDIS_URL: redis://redis:6379/0" in compose
    assert "QDRANT_URL: http://qdrant:6333" in compose
    assert "QDRANT_MEMORY_COLLECTION:" in compose
    assert "QDRANT_VECTOR_SIZE:" in compose
    assert "DOCS_DB:" not in compose
    assert "CHROMA_DB_PATH:" not in compose
    assert "condition: service_completed_successfully" in compose
    assert 'command: ["alembic", "upgrade", "head"]' in compose
    assert "app_data:/app/data" in compose
    assert "./data/laws:/app/data/laws:ro" in compose


def test_docker_build_does_not_copy_secrets_or_models() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert ".env" in dockerignore
    # 锚定到上下文根目录，否则会连 infrastructure/models/ 的 ORM 源码一起排除。
    assert "/models" in dockerignore
    assert "models" not in dockerignore
    assert "https://download.pytorch.org/whl/cpu" in dockerfile
    assert '"torch>=2.2,<3"' in dockerfile


def test_removed_storage_backends_are_not_runtime_dependencies() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "chromadb" not in requirements
    assert "aiosqlite" not in requirements


def test_container_entrypoint_has_remote_model_fallbacks() -> None:
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

    assert "BAAI/bge-small-zh-v1.5" in entrypoint
    assert "BAAI/bge-reranker-base" in entrypoint
    assert 'exec "$@"' in entrypoint


def test_compose_is_cpu_only() -> None:
    """部署栈固定跑 CPU：不请求 GPU、不装 CUDA wheel、也不再有覆盖文件。"""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "https://download.pytorch.org/whl/cpu" in compose
    # 写死而不是 ${MODEL_DEVICE:-cpu}：compose 会用 .env 做插值，旧 .env 里的 cuda 会顶掉默认值。
    assert "MODEL_DEVICE: cpu" in compose
    assert "MODEL_DEVICE: ${" not in compose
    assert "gpus:" not in compose
    assert "cu130" not in compose
    assert not (ROOT / "docker-compose.cpu.yml").exists()
