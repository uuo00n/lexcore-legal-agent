"""法律文书起草工具 —— 通过 MCP Client 调用 MCP Server 的文书生成能力。"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from services.mcp_client import call_tool


@tool
async def legal_document_draft_tool(doc_type: str, key_facts: dict[str, Any]) -> str:
    """
    函数作用：
        根据文书类型和关键事实生成法律文书草稿。
    输入参数：
        - doc_type: str
        - key_facts: dict[str, Any]
    输出参数：
        - str
    """
    return await call_tool(
        "legal_document_draft",
        {"doc_type": doc_type, "key_facts": key_facts},
    )
