"""诉讼时效 LangChain Tool；直接调用共享 Service。"""
from __future__ import annotations

from langchain_core.tools import tool

from services.legal_tools import statute_of_limitations_service


@tool
async def statute_of_limitations_tool(event_date: str, case_type: str) -> str:
    """根据案由和事件日期计算诉讼时效截止日。"""
    return statute_of_limitations_service(event_date, case_type)
