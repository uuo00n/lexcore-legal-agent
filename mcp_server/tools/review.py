"""合同审查 FastMCP 暴露层。"""
from __future__ import annotations

from mcp_server.server import mcp
from services.legal_tools import contract_review_service


@mcp.tool()
def contract_review(contract_text: str, focus_areas: str = "") -> str:
    """检索合同相关法条并生成审查上下文。"""
    return contract_review_service(contract_text, focus_areas)
