"""管辖与办理路径工具 —— 通过 MCP Client 调用 MCP Server 的路径判断能力。"""
from __future__ import annotations

from langchain_core.tools import tool

from services.mcp_client import call_tool


@tool
async def jurisdiction_tool(
    case_type: str,
    location: str = "",
    parties: str = "",
    contract_clause: str = "",
) -> str:
    """
    函数作用：
        根据案件类型、地点、双方身份和合同约定，判断去哪申请、投诉、仲裁或起诉。
    输入参数：
        - case_type: str
        - location: str，默认值 ''
        - parties: str，默认值 ''
        - contract_clause: str，默认值 ''
    输出参数：
        - str
    """
    return await call_tool(
        "jurisdiction_route",
        {
            "case_type": case_type,
            "location": location,
            "parties": parties,
            "contract_clause": contract_clause,
        },
    )
