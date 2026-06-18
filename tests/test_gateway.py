from __future__ import annotations

import pytest

from services.checkpoint import init_meta_db, reset_for_tests
from services.gateway import GatewayChatModel, LLMClientConfig
from services.observability import init_observability_tables, list_llm_calls


class FakeClient:
    def __init__(self, *, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.bound = False

    def bind_tools(self, tools, **kwargs):
        clone = FakeClient(response=self.response, error=self.error)
        clone.bound = True
        return clone

    async def ainvoke(self, input, **kwargs):
        if self.error:
            raise self.error
        return self.response


class FakeResponse:
    response_metadata = {"token_usage": {"total_tokens": 12}}


def setup_function():
    reset_for_tests()


def teardown_function():
    reset_for_tests()


@pytest.mark.asyncio
async def test_gateway_logs_success(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DB", str(tmp_path / "meta.sqlite"))
    init_meta_db()
    init_observability_tables()
    gateway = GatewayChatModel([
        LLMClientConfig("primary", "model-a", "http://primary", "fast", FakeClient(response=FakeResponse()))
    ], trace_id="trace-1", thread_id="thread-1")

    response = await gateway.ainvoke("hello")

    assert isinstance(response, FakeResponse)
    calls = list_llm_calls()
    assert calls[0]["status"] == "success"
    assert calls[0]["total_tokens"] == 12


@pytest.mark.asyncio
async def test_gateway_falls_back_after_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DB", str(tmp_path / "meta.sqlite"))
    init_meta_db()
    init_observability_tables()
    gateway = GatewayChatModel([
        LLMClientConfig("primary", "model-a", "http://primary", "strong", FakeClient(error=RuntimeError("boom"))),
        LLMClientConfig("backup", "model-b", "http://backup", "strong", FakeClient(response=FakeResponse())),
    ], trace_id="trace-1", thread_id="thread-1")

    response = await gateway.ainvoke("hello")

    assert isinstance(response, FakeResponse)
    calls = {item["provider"]: item for item in list_llm_calls()}
    assert calls["primary"]["status"] == "error"
    assert calls["backup"]["status"] == "success"
    assert calls["backup"]["fallback_from"] == "primary"
    assert calls["backup"]["model_route"] == "strong"
