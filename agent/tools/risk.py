"""法律风险评估工具 —— 通过 MCP Client 调用 MCP Server 的风险评估能力。"""
from __future__ import annotations

from langchain_core.tools import tool

from services.mcp_client import call_tool


@tool
async def risk_assess_tool(facts: str) -> str:
    """
    函数作用：
        根据用户描述的事实情况，检索相关法条并评估法律风险。
    输入参数：
        - facts: str
    输出参数：
        - str
    """
    return await call_tool("risk_assess", {"situation": facts})
