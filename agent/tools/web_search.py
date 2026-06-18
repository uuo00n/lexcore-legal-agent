"""联网搜索兜底工具 —— 本地法库无结果时通过网络搜索补充。"""
from __future__ import annotations

from langchain_core.tools import tool

from services.mcp_client import call_tool


@tool
async def web_search_tool(query: str, max_results: int = 5) -> str:
    """
    函数作用：
        当本地法律数据库检索无相关结果时，通过网络搜索获取补充信息。
    输入参数：
        - query: str
        - max_results: int，默认值 5
    输出参数：
        - str
    """
    return await call_tool("web_search_fallback", {"query": query, "max_results": max_results})
