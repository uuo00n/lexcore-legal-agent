"""法条对比 LangChain Tool；直接调用共享 Service。"""
from __future__ import annotations

import asyncio

from langchain_core.tools import tool

from services.legal_tools import law_compare_service


@tool
async def law_compare_tool(law_a: str, law_b: str, topic: str) -> str:
    """对比两部法律在某个主题上的条款异同。"""
    return await asyncio.to_thread(law_compare_service, law_a, law_b, topic)
