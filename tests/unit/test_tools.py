"""Tool failure normalization and transport retry tests."""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

from agent.state import AgentState
from agent.tools import search_law_tool
from services.delilegal.exceptions import DelilegalAuthenticationError
from services.errors import RetrievalError
from services.retry import RetryPolicy, retry_async


async def _invoke_law_tool() -> dict:
    graph = StateGraph(AgentState)
    graph.add_node("tools", ToolNode([search_law_tool]))
    graph.set_entry_point("tools")
    graph.set_finish_point("tools")
    return await graph.compile().ainvoke({
        "trace_id": "api-failure-test",
        "messages": [AIMessage(
            content="",
            tool_calls=[{
                "name": "search_law_tool",
                "args": {"query": "劳动合同解除"},
                "id": "call-1",
                "type": "tool_call",
            }],
        )],
    })


async def test_api_tool_failure_returns_safe_structured_observation(monkeypatch):
    class FailingClient:
        def __init__(self, *, trace_id=None):
            self.trace_id = trace_id

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def search_laws(self, _request):
            raise DelilegalAuthenticationError("bad appid=private secret=private")

    monkeypatch.setattr("services.search.DelilegalClient", FailingClient)
    monkeypatch.setattr("services.search._record_tool_event", lambda *_args, **_kwargs: None)

    result = await _invoke_law_tool()
    message = result["messages"][-1]
    payload = json.loads(message.content)

    assert message.status == "error"
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "authentication_failed"
    assert payload["trace_id"] == "api-failure-test"
    assert "private" not in message.content


async def test_retry_recovers_from_two_transient_failures():
    attempts = 0

    async def flaky_operation():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RetrievalError("temporary", retryable=True)
        return "ok"

    result = await retry_async(
        flaky_operation,
        operation_name="unit.transient",
        policy=RetryPolicy(max_attempts=3, multiplier=0, min_wait=0, max_wait=0),
    )

    assert result == "ok"
    assert attempts == 3


async def test_retry_does_not_repeat_permanent_tool_failure():
    attempts = 0

    async def invalid_operation():
        nonlocal attempts
        attempts += 1
        raise RetrievalError("invalid query", retryable=False)

    try:
        await retry_async(
            invalid_operation,
            operation_name="unit.permanent",
            policy=RetryPolicy(max_attempts=3, multiplier=0, min_wait=0, max_wait=0),
        )
    except RetrievalError:
        pass
    else:
        raise AssertionError("permanent failure should be raised")

    assert attempts == 1
