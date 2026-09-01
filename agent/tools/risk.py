"""法律风险评估 LangChain Tool；直接调用共享 Service。"""
from __future__ import annotations

import asyncio

from langchain_core.tools import tool

from services.legal_tools import risk_assess_service


@tool
async def risk_assess_tool(facts: str) -> str:
    """根据事实描述检索法条并生成风险分析上下文。"""
    return await asyncio.to_thread(risk_assess_service, facts)
