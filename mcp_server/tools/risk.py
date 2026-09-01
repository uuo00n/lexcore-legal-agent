"""风险评估 MCP 工具 —— 根据事实描述检索法条并评估法律风险。"""
from __future__ import annotations

import json

from mcp_server.server import mcp
from services.rag.retriever import get_retriever


@mcp.tool()
def risk_assess(situation: str) -> str:
    """
    函数作用：
        根据用户描述的事实情况，检索相关法条并评估法律风险。
    输入参数：
        - situation: str
    输出参数：
        - str
    """
    retriever = get_retriever()
    chunks = retriever.retrieve(situation, top_k=8)

    result = {
        "situation": situation,
        "relevant_laws": [
            {
                "law_name": c.law_name,
                "article_no": c.article_no,
                "hierarchy": c.hierarchy,
                "content": c.content,
            }
            for c in chunks
        ],
        "analysis_hint": "请基于以上法条，分析事实情况中的法律风险点，给出风险等级（高/中/低）和应对建议。",
    }
    return json.dumps(result, ensure_ascii=False)
