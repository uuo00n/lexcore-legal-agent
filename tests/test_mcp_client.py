from __future__ import annotations

import asyncio

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR


class _TextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _ToolResult:
    def __init__(self, text: str) -> None:
        self.content = [_TextBlock(text)]


class _FakeSession:
    def __init__(self) -> None:
        self.timeout = "not-called"

    async def call_tool(self, name, arguments, read_timeout_seconds=None):
        self.timeout = read_timeout_seconds
        return _ToolResult(f"{name}:{arguments['query']}")


class _TimeoutSession:
    async def call_tool(self, name, arguments, read_timeout_seconds=None):
        raise TimeoutError("tool took too long")


class _McpTimeoutSession:
    async def call_tool(self, name, arguments, read_timeout_seconds=None):
        raise McpError(ErrorData(
            code=INTERNAL_ERROR,
            message="Timed out while waiting for response to ClientRequest. Waited 45.0 seconds.",
        ))


@pytest.mark.asyncio
async def test_call_tool_does_not_pass_read_timeout(monkeypatch):
    from services import mcp_client

    fake = _FakeSession()
    monkeypatch.setattr(mcp_client, "_session", fake)

    result = await mcp_client.call_tool("legal_search", {"query": "押金"})

    assert result == "legal_search:押金"
    assert fake.timeout is None


@pytest.mark.asyncio
async def test_call_tool_propagates_timeout_error(monkeypatch):
    from services import mcp_client

    monkeypatch.setattr(mcp_client, "_session", _TimeoutSession())

    with pytest.raises(TimeoutError):
        await mcp_client.call_tool("legal_search", {"query": "醉驾"})


@pytest.mark.asyncio
async def test_call_tool_propagates_mcp_timeout_error(monkeypatch):
    from services import mcp_client

    monkeypatch.setattr(mcp_client, "_session", _McpTimeoutSession())

    with pytest.raises(McpError):
        await mcp_client.call_tool("legal_search", {"query": "劳动合同"})


@pytest.mark.asyncio
async def test_local_rag_tools_are_serialized_but_other_tools_can_run_concurrently(monkeypatch):
    from services import mcp_client

    started: list[str] = []
    release_legal = asyncio.Event()
    web_finished = asyncio.Event()

    class _ConcurrentSession:
        async def call_tool(self, name, arguments, read_timeout_seconds=None):
            started.append(name)
            if name == "legal_search":
                await release_legal.wait()
            if name == "web_search_fallback":
                web_finished.set()
            return _ToolResult(f"{name}:ok")

    monkeypatch.setattr(mcp_client, "_session", _ConcurrentSession())
    monkeypatch.setattr(mcp_client, "_tool_limiters", {})

    legal_task = asyncio.create_task(mcp_client.call_tool("legal_search", {"query": "借款"}))
    await asyncio.sleep(0)

    web_task = asyncio.create_task(mcp_client.call_tool("web_search_fallback", {"query": "案例"}))
    await asyncio.wait_for(web_finished.wait(), timeout=0.5)

    assert started == ["legal_search", "web_search_fallback"]
    assert web_task.done()
    assert await web_task == "web_search_fallback:ok"
    assert not legal_task.done()

    release_legal.set()
    assert await legal_task == "legal_search:ok"


@pytest.mark.asyncio
async def test_local_rag_tools_run_one_at_a_time(monkeypatch):
    from services import mcp_client

    started: list[str] = []
    release_first = asyncio.Event()

    class _SerialSession:
        async def call_tool(self, name, arguments, read_timeout_seconds=None):
            started.append(arguments["query"])
            if arguments["query"] == "first":
                await release_first.wait()
            return _ToolResult(f"{name}:{arguments['query']}")

    monkeypatch.setattr(mcp_client, "_session", _SerialSession())
    monkeypatch.setattr(mcp_client, "_tool_limiters", {})

    first = asyncio.create_task(mcp_client.call_tool("legal_search", {"query": "first"}))
    await asyncio.sleep(0)
    second = asyncio.create_task(mcp_client.call_tool("legal_search", {"query": "second"}))
    await asyncio.sleep(0)

    assert started == ["first"]
    assert not second.done()

    release_first.set()
    assert await first == "legal_search:first"
    assert await second == "legal_search:second"
    assert started == ["first", "second"]
