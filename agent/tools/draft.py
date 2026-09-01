"""法律文书起草 LangChain Tool；直接调用共享 Service。"""
from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.tools import tool

from services.legal_tools import legal_document_draft_service


@tool
async def legal_document_draft_tool(
    doc_type: str,
    key_facts: dict[str, Any],
) -> str:
    """根据文书类型和关键事实生成法律文书草稿。"""
    return await asyncio.to_thread(legal_document_draft_service, doc_type, key_facts)
