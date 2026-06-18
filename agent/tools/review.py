"""合同审查工具 —— 通过 MCP Client 调用 MCP Server 的合同审查能力。"""
from __future__ import annotations

from langchain_core.tools import tool

from services.mcp_client import call_tool


@tool
async def contract_review_tool(contract_text: str, focus: str = "") -> str:
    """
    函数作用：
        审查合同或法律文件，结合相关法条指出潜在问题。
    输入参数：
        - contract_text: str
        - focus: str，默认值 ''
    输出参数：
        - str
    """
    return await call_tool("contract_review", {"contract_text": contract_text, "focus_areas": focus})
