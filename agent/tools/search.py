"""RAG 法律检索工具 —— 通过 MCP Client 调用 MCP Server 的检索能力。"""
from __future__ import annotations

from langchain_core.tools import tool

from services.mcp_client import call_tool


@tool
async def legal_search_tool(query: str, top_k: int = 5) -> str:
    """
    函数作用：
        根据用户问题检索相关中国法律条款。
    输入参数：
        - query: str
        - top_k: int，默认值 5
    输出参数：
        - str
    """
    return await call_tool("legal_search", {"query": query, "top_k": top_k})
