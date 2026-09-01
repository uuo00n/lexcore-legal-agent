"""管辖与办理路径 LangChain Tool；直接调用共享 Service。"""
from __future__ import annotations

from langchain_core.tools import tool

from services.jurisdiction import jurisdiction_route_service


@tool
async def jurisdiction_tool(
    case_type: str,
    location: str = "",
    parties: str = "",
    contract_clause: str = "",
) -> str:
    """判断去哪申请、投诉、仲裁或起诉。"""
    return jurisdiction_route_service(
        case_type=case_type,
        location=location,
        parties=parties,
        contract_clause=contract_clause,
    )
