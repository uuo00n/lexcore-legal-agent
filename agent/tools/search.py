"""本地 DOC 法律 RAG 工具。"""
from __future__ import annotations

from langchain_core.tools import tool

from services.mcp_client import call_tool


@tool
async def retrieve_local_law_tool(query: str, top_k: int = 5) -> str:
    """
    函数作用：
        只检索项目已经索引的本地 DOC 中国法律知识库；不访问外部网络。
    输入参数：
        - query: str
        - top_k: int，默认值 5
    输出参数：
        - str
    """
    return await call_tool("legal_search", {"query": query, "top_k": top_k})
