"""诉讼时效 FastMCP 暴露层。"""
from __future__ import annotations

from mcp_server.server import mcp
from services.legal_tools import statute_of_limitations_service


@mcp.tool()
def statute_of_limitations(event_date: str, case_type: str) -> str:
    """计算常见案件的诉讼时效截止日期。"""
    return statute_of_limitations_service(event_date, case_type)
