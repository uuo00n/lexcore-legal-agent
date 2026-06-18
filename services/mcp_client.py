"""MCP Client —— 通过 stdio 连接 MCP Server，统一管理工具调用。

Agent 侧所有工具调用通过本模块转发到 MCP Server，
实现工具逻辑集中管理、agent 与工具实现解耦。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import AsyncExitStack
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

log = logging.getLogger("legal.mcp_client")

_session: Optional[ClientSession] = None
_exit_stack: Optional[AsyncExitStack] = None
_tool_limiters: dict[str, asyncio.Semaphore] = {}

_LOCAL_RAG_TOOLS = {"legal_search"}


async def start_mcp_client() -> None:
    """
    函数作用：
        启动 MCP Client，通过 stdio 连接到 MCP Server 子进程。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    global _session, _exit_stack

    server_script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "run_mcp.py",
    )

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[server_script],
        env={**os.environ},
    )

    _exit_stack = AsyncExitStack()
    await _exit_stack.__aenter__()

    read_stream, write_stream = await _exit_stack.enter_async_context(
        stdio_client(server_params)
    )

    session = await _exit_stack.enter_async_context(
        ClientSession(read_stream, write_stream)
    )

    await session.initialize()
    _session = session

    tools = await session.list_tools()
    tool_names = [t.name for t in tools.tools]
    log.info("MCP Client 已连接，可用工具: %s", tool_names)


async def stop_mcp_client() -> None:
    """
    函数作用：
        关闭 MCP Client 连接和 MCP Server 子进程。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    global _session, _exit_stack, _tool_limiters

    if _exit_stack:
        await _exit_stack.aclose()

    _session = None
    _exit_stack = None
    _tool_limiters = {}
    log.info("MCP Client 已关闭")


def _local_rag_concurrency() -> int:
    return max(1, int(os.getenv("MCP_LOCAL_RAG_CONCURRENCY", "1")))


def _tool_resource_group(name: str) -> str | None:
    if name in _LOCAL_RAG_TOOLS:
        return "local_rag"
    return None


def _get_tool_limiter(group: str) -> asyncio.Semaphore:
    if group not in _tool_limiters:
        limit = _local_rag_concurrency() if group == "local_rag" else 9999
        _tool_limiters[group] = asyncio.Semaphore(limit)
    return _tool_limiters[group]


async def _call_tool_with_limits(name: str, arguments: dict[str, Any]):
    group = _tool_resource_group(name)
    if group is None:
        return await _session.call_tool(name, arguments)
    async with _get_tool_limiter(group):
        return await _session.call_tool(name, arguments)


async def call_tool(name: str, arguments: dict[str, Any]) -> str:
    """
    函数作用：
        调用 MCP Server 上的工具。
    输入参数：
        - name: str
        - arguments: dict[str, Any]
    输出参数：
        - str
    """
    if _session is None:
        raise RuntimeError("MCP Client 未初始化，请先调用 start_mcp_client()")

    result = await _call_tool_with_limits(name, arguments)

    parts = []
    for block in result.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts)
