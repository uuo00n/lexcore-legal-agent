"""智能体工具集 —— 导出所有可用工具。

本包将原有的单文件 agent/tools.py 拆分为多工具模块，
每个工具独立一个文件，通过本 __init__.py 统一导出。
所有工具通过 MCP Client 调用 MCP Server 执行。
"""
from __future__ import annotations

from agent.tools.search import legal_search_tool
from agent.tools.compare import law_compare_tool
from agent.tools.risk import risk_assess_tool
from agent.tools.review import contract_review_tool
from agent.tools.limitations import statute_of_limitations_tool
from agent.tools.jurisdiction import jurisdiction_tool
from agent.tools.draft import legal_document_draft_tool
from agent.tools.web_search import web_search_tool

# 所有工具列表，注册到 LangGraph ToolNode
ALL_TOOLS = [
    legal_search_tool,
    law_compare_tool,
    risk_assess_tool,
    contract_review_tool,
    statute_of_limitations_tool,
    jurisdiction_tool,
    legal_document_draft_tool,
    web_search_tool,
]

LEGAL_CONSULT_TOOLS = [
    legal_search_tool,
    web_search_tool,
    statute_of_limitations_tool,
    jurisdiction_tool,
]

__all__ = [
    "ALL_TOOLS",
    "LEGAL_CONSULT_TOOLS",
    "legal_search_tool",
    "law_compare_tool",
    "risk_assess_tool",
    "contract_review_tool",
    "statute_of_limitations_tool",
    "jurisdiction_tool",
    "legal_document_draft_tool",
    "web_search_tool",
]
