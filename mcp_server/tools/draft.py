"""法律文书起草 FastMCP 暴露层。"""
from __future__ import annotations

from typing import Any

from mcp_server.server import mcp
from services.legal_tools import legal_document_draft_service


@mcp.tool()
def legal_document_draft(doc_type: str, key_facts: dict[str, Any]) -> str:
    """根据模板和关键事实生成法律文书草稿。"""
    return legal_document_draft_service(doc_type, key_facts)
