"""合同审查 MCP 工具 —— 审查合同文本，结合法条指出问题。"""
from __future__ import annotations

import json

from mcp_server.server import mcp
from services.retriever import get_retriever


@mcp.tool()
def contract_review(contract_text: str, focus_areas: str = "") -> str:
    """
    函数作用：
        审查合同文本，检索相关法条并指出潜在问题。
    输入参数：
        - contract_text: str
        - focus_areas: str，默认值 ''
    输出参数：
        - str
    """
    retriever = get_retriever()

    # 构造检索 query
    query = focus_areas + " " + contract_text[:200] if focus_areas else contract_text[:500]
    chunks = retriever.retrieve(query.strip(), top_k=8)

    # 合同文本截断（避免输出过长）
    truncated = contract_text[:3000]
    is_truncated = len(contract_text) > 3000

    result = {
        "contract_text": truncated,
        "is_truncated": is_truncated,
        "focus_areas": focus_areas or "全面审查",
        "relevant_laws": [
            {
                "law_name": c.law_name,
                "article_no": c.article_no,
                "content": c.content,
            }
            for c in chunks
        ],
        "review_hint": "请基于以上法条审查合同条款，指出不合规或对一方明显不利的条款，并给出修改建议。",
    }
    return json.dumps(result, ensure_ascii=False)
