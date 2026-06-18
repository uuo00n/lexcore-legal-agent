"""法条对比工具 —— 通过 MCP Client 调用 MCP Server 的对比能力。"""
from __future__ import annotations

from langchain_core.tools import tool

from services.mcp_client import call_tool


@tool
async def law_compare_tool(law_a: str, law_b: str, topic: str) -> str:
    """
    函数作用：
        对比两部法律在某个主题上的条款异同。
    输入参数：
        - law_a: str
        - law_b: str
        - topic: str
    输出参数：
        - str
    """
    return await call_tool("law_compare", {"law_a": law_a, "law_b": law_b, "topic": topic})
