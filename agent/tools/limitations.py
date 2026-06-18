"""诉讼时效工具 —— 通过 MCP Client 调用 MCP Server 的时效计算能力。"""
from __future__ import annotations

from langchain_core.tools import tool

from services.mcp_client import call_tool


@tool
async def statute_of_limitations_tool(event_date: str, case_type: str) -> str:
    """
    函数作用：
        根据案由和事件日期计算诉讼时效截止日。
    输入参数：
        - event_date: str
        - case_type: str
    输出参数：
        - str
    """
    return await call_tool(
        "statute_of_limitations",
        {"event_date": event_date, "case_type": case_type},
    )
