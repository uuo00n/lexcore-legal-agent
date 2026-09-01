"""法条对比 FastMCP 暴露层。"""
from __future__ import annotations

from mcp_server.server import mcp
from services.legal_tools import law_compare_service


@mcp.tool()
def law_compare(law_a: str, law_b: str, topic: str) -> str:
    """对比两部法律在指定主题下的相关条款。"""
    return law_compare_service(law_a, law_b, topic)
