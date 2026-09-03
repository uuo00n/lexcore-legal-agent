"""Shared deterministic fixtures for unit and integration tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_redis(monkeypatch):
    """禁止单元测试连接真实 Redis。

    `main.py` 在导入时执行 `load_dotenv()`，会把开发机 `.env` 里的 REDIS_URL 注入 os.environ。
    只要本机恰好跑着 Redis（`docker compose up -d postgres redis qdrant` 之后就是这样），
    缓存包装层就会命中真实实例：检索用例读到上一次运行留下的缓存，既拿不到调用记录也拿不到
    trace 事件，测试结果随本机 Redis 内容漂移。这里统一按未启用处理，需要 Redis 行为的用例
    自行注入替身客户端。
    """
    monkeypatch.setenv("REDIS_ENABLED", "false")
    from infrastructure import redis as redis_infra

    redis_infra.reset_for_tests()
    yield
    redis_infra.reset_for_tests()


@pytest.fixture(scope="session")
def legal_scenarios() -> dict:
    fixture_path = Path(__file__).parent / "fixtures" / "legal_scenarios.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


@pytest.fixture
def grounded_law(legal_scenarios: dict) -> dict:
    return dict(legal_scenarios["law"])
