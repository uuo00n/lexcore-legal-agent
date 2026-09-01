"""统一错误类型、传输重试与 Agent Replan 的回归测试。"""
from __future__ import annotations

import json

import httpx
import pytest

from agent.nodes.routing import should_after_verifier
from agent.tool_loop import tool_error_observation
from services.errors import (
    CacheError,
    DatabaseError,
    DelilegalAPIError,
    LLMError,
    RetrievalError,
    ToolError,
)
from services.retry import RetryPolicy, is_retryable_exception, retry_async, retry_sync


NO_WAIT = RetryPolicy(max_attempts=3, multiplier=0, min_wait=0, max_wait=0)


def test_unified_error_types_are_distinct_and_non_retryable_by_default():
    error_types = {
        LLMError,
        ToolError,
        RetrievalError,
        DelilegalAPIError,
        DatabaseError,
        CacheError,
    }

    assert len(error_types) == 6
    assert all(error_type("failed").retryable is False for error_type in error_types)


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("slow"),
        ConnectionError("disconnected"),
        httpx.ConnectError("unreachable"),
        LLMError("temporary", retryable=True),
        DelilegalAPIError("server error", status_code=503, retryable=True),
    ],
)
def test_retryable_classifier_accepts_only_transient_failures(error):
    assert is_retryable_exception(error) is True


@pytest.mark.parametrize(
    "error",
    [
        ValueError("invalid user input"),
        ToolError("schema invalid"),
        DelilegalAPIError("bad request", status_code=400),
        DelilegalAPIError("authentication failed", status_code=401),
    ],
)
def test_retryable_classifier_rejects_permanent_failures(error):
    assert is_retryable_exception(error) is False


async def test_async_retry_stops_after_three_transient_attempts():
    attempts = 0

    async def fail():
        nonlocal attempts
        attempts += 1
        raise LLMError("temporary", retryable=True)

    with pytest.raises(LLMError):
        await retry_async(fail, operation_name="test.async", policy=NO_WAIT)

    assert attempts == 3


def test_sync_retry_does_not_repeat_schema_error():
    attempts = 0

    def fail():
        nonlocal attempts
        attempts += 1
        raise RetrievalError("schema invalid")

    with pytest.raises(RetrievalError):
        retry_sync(fail, operation_name="test.sync", policy=NO_WAIT)

    assert attempts == 1


def test_tool_observation_does_not_blindly_retry_input_error():
    permanent = json.loads(tool_error_observation(ValueError("invalid arguments")))
    transient = json.loads(
        tool_error_observation(ToolError("temporary connection", retryable=True))
    )

    assert permanent["retryable"] is False
    assert transient["retryable"] is True


def test_agent_replan_budget_is_independent_and_bounded_to_one():
    first = {
        "verification_result": {"needs_retry": True},
        "supervisor_route": "replan",
        "replan_retry_count": 1,
        "retry_count": 99,
    }
    exhausted = {**first, "replan_retry_count": 2}

    assert should_after_verifier(first) == "replan"
    assert should_after_verifier(exhausted) == "answer_generator"
