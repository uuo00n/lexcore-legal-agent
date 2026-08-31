"""智能体工具集 —— 导出所有可用工具。

本包将原有的单文件 agent/tools.py 拆分为多工具模块，
每个工具独立一个文件，通过本 __init__.py 统一导出。
本地 RAG 与既有确定性工具通过 MCP 执行，得理检索工具通过统一 Service Layer 执行。
"""
from __future__ import annotations

from agent.tools.search import retrieve_local_law_tool
from agent.tools.delilegal import search_case_tool, search_law_tool
from agent.tools.compare import law_compare_tool
from agent.tools.risk import risk_assess_tool
from agent.tools.review import contract_review_tool
from agent.tools.limitations import statute_of_limitations_tool
from agent.tools.jurisdiction import jurisdiction_tool
from agent.tools.draft import legal_document_draft_tool

# 所有工具列表，注册到 LangGraph ToolNode
ALL_TOOLS = [
    retrieve_local_law_tool,
    search_law_tool,
    search_case_tool,
    law_compare_tool,
    risk_assess_tool,
    contract_review_tool,
    statute_of_limitations_tool,
    jurisdiction_tool,
    legal_document_draft_tool,
]

LEGAL_CONSULT_TOOLS = [
    retrieve_local_law_tool,
    search_law_tool,
    search_case_tool,
    statute_of_limitations_tool,
    jurisdiction_tool,
]

STATUTE_RETRIEVAL_TOOLS = [retrieve_local_law_tool, search_law_tool]

CASE_ANALYSIS_TOOLS = [search_case_tool, search_law_tool, retrieve_local_law_tool]

__all__ = [
    "ALL_TOOLS",
    "LEGAL_CONSULT_TOOLS",
    "STATUTE_RETRIEVAL_TOOLS",
    "CASE_ANALYSIS_TOOLS",
    "retrieve_local_law_tool",
    "search_law_tool",
    "search_case_tool",
    "law_compare_tool",
    "risk_assess_tool",
    "contract_review_tool",
    "statute_of_limitations_tool",
    "jurisdiction_tool",
    "legal_document_draft_tool",
]
