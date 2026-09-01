"""合同审查 LangChain Tool；直接调用共享 Service。"""
from __future__ import annotations

import asyncio

from langchain_core.tools import tool

from services.legal_tools import contract_review_service


@tool
async def contract_review_tool(contract_text: str, focus: str = "") -> str:
    """审查合同或法律文件，并提供相关法律依据。"""
    return await asyncio.to_thread(contract_review_service, contract_text, focus)
