"""风险评估 FastMCP 暴露层。"""
from __future__ import annotations

from mcp_server.server import mcp
from services.legal_tools import risk_assess_service


@mcp.tool()
def risk_assess(situation: str) -> str:
    """检索事实相关法条并生成风险分析上下文。"""
    return risk_assess_service(situation)
