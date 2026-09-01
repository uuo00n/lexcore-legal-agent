"""Specialist Agent 工具注册表。"""
from __future__ import annotations

from agent.tools.rag_search import retrieve_local_law_tool
from agent.tools.case_search import search_case_tool
from agent.tools.law_search import search_law_tool

LEGAL_CONSULT_TOOLS = [
    search_law_tool,
    retrieve_local_law_tool,
]

STATUTE_RETRIEVAL_TOOLS = [search_law_tool, retrieve_local_law_tool]

CASE_ANALYSIS_TOOLS = [search_case_tool, search_law_tool, retrieve_local_law_tool]

__all__ = [
    "LEGAL_CONSULT_TOOLS",
    "STATUTE_RETRIEVAL_TOOLS",
    "CASE_ANALYSIS_TOOLS",
    "retrieve_local_law_tool",
    "search_law_tool",
    "search_case_tool",
]
