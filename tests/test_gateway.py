from __future__ import annotations

import pytest

from infrastructure.operational_store import InMemoryOperationalStore, init_operational_store
from services.checkpoint import reset_for_tests
from services.gateway import GatewayChatModel, LLMClientConfig
from services.errors import LLMError
from services.retry import RetryPolicy, retry_async
from services.observability import (
    create_trace,
    get_trace,
    list_llm_calls,
)


class FakeClient:
    def __init__(self, *, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.bound = False

    def bind_tools(self, tools, **kwargs):
        clone = FakeClient(response=self.response, error=self.error)
        clone.bound = True
        return clone

    def with_structured_output(self, schema, **kwargs):
        return FakeClient(response=self.response, error=self.error)

    async def ainvoke(self, input, **kwargs):
        if self.error:
            raise self.error
        return self.response


class FakeResponse:
    response_metadata = {"token_usage": {"total_tokens": 12}}


class SequentialClient(FakeClient):
    def __init__(self, outcomes):
        super().__init__()
        self.outcomes = list(outcomes)
        self.calls = 0

    async def ainvoke(self, input, **kwargs):
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _disable_retry_wait(monkeypatch):
    async def fast_retry(operation, *, operation_name):
        return await retry_async(
            operation,
            operation_name=operation_name,
            policy=RetryPolicy(max_attempts=3, multiplier=0, min_wait=0, max_wait=0),
        )

    monkeypatch.setattr("services.gateway.retry_async", fast_retry)


def setup_function():
    reset_for_tests()
    init_operational_store(InMemoryOperationalStore())


def teardown_function():
    reset_for_tests()


@pytest.mark.asyncio
async def test_gateway_logs_success():
    create_trace("trace-1", "thread-1", "hello")
    gateway = GatewayChatModel([
        LLMClientConfig("primary", "model-a", "http://primary", "fast", FakeClient(response=FakeResponse()))
    ], trace_id="trace-1", thread_id="thread-1")

    response = await gateway.ainvoke("hello")

    assert isinstance(response, FakeResponse)
    calls = list_llm_calls()
    assert calls[0]["status"] == "success"
    assert calls[0]["total_tokens"] == 12
    event = get_trace("trace-1")["events"][0]
    assert event["event_type"] == "llm_call"
    assert event["payload"]["model"] == "model-a"
    assert event["payload"]["token_usage"]["total_tokens"] == 12
    assert event["payload"]["success"] is True


@pytest.mark.asyncio
async def test_gateway_falls_back_after_error():
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


def test_structured_output_keeps_gateway_wrapper():
    gateway = GatewayChatModel([
        LLMClientConfig("primary", "model-a", "http://primary", "planner", FakeClient())
    ])

    structured = gateway.with_structured_output(dict)

    assert isinstance(structured, GatewayChatModel)


@pytest.mark.asyncio
async def test_gateway_retries_timeout_but_not_permanent_error(monkeypatch):
    _disable_retry_wait(monkeypatch)
    transient = SequentialClient([TimeoutError("slow"), TimeoutError("slow"), FakeResponse()])
    gateway = GatewayChatModel([
        LLMClientConfig("primary", "model-a", "http://primary", "planner", transient)
    ])

    assert isinstance(await gateway.ainvoke("hello"), FakeResponse)
    assert transient.calls == 3

    permanent = SequentialClient([ValueError("schema invalid"), FakeResponse()])
    gateway = GatewayChatModel([
        LLMClientConfig("primary", "model-a", "http://primary", "planner", permanent)
    ])

    with pytest.raises(LLMError) as error:
        await gateway.ainvoke("hello")
    assert error.value.retryable is False
    assert permanent.calls == 1


@pytest.mark.asyncio
async def test_gateway_error_event_keeps_provider_message_and_status_code():
    create_trace("trace-1", "thread-1", "hello")

    class BadRequest(Exception):
        status_code = 400

    provider_message = (
        "Error code: 400 - {'error': {'message': 'This response_format type is unavailable now', "
        "'type': 'invalid_request_error'}}"
    )
    gateway = GatewayChatModel([
        LLMClientConfig("deepseek", "deepseek-v4-pro", "https://api.deepseek.com", "planner",
                        FakeClient(error=BadRequest(provider_message)))
    ], trace_id="trace-1", thread_id="thread-1")

    with pytest.raises(LLMError):
        await gateway.ainvoke("hello")

    payload = get_trace("trace-1")["events"][0]["payload"]
    # 自家兜底文案会把所有失败压成同一句，provider 原文与状态码必须留在事件里。
    assert payload["status_code"] == 400
    assert "This response_format type is unavailable now" in payload["provider_error"]
    assert payload["provider_error_type"] == "BadRequest"
    assert "This response_format type is unavailable now" in list_llm_calls()[0]["error"]
