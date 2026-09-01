"""管辖与办理路径 FastMCP 暴露层。"""
from __future__ import annotations

from mcp_server.server import mcp
from services.jurisdiction import jurisdiction_route_service


@mcp.tool()
def jurisdiction_route(
    case_type: str,
    location: str = "",
    parties: str = "",
    contract_clause: str = "",
) -> str:
    """根据案件事实返回常见办理机关和管辖连接点。"""
    return jurisdiction_route_service(
        case_type=case_type,
        location=location,
        parties=parties,
        contract_clause=contract_clause,
    )


__all__ = ["jurisdiction_route"]
