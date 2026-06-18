"""法条对比 MCP 工具 —— 对比两部法律在某主题上的条款异同。"""
from __future__ import annotations

import json

from mcp_server.server import mcp
from services.retriever import get_retriever


@mcp.tool()
def law_compare(law_a: str, law_b: str, topic: str) -> str:
    """
    函数作用：
        对比两部法律在某个主题上的条款异同。
    输入参数：
        - law_a: str
        - law_b: str
        - topic: str
    输出参数：
        - str
    """
    retriever = get_retriever()

    chunks_a = retriever.retrieve(f"{law_a} {topic}", top_k=5)
    chunks_b = retriever.retrieve(f"{law_b} {topic}", top_k=5)

    filtered_a = [c for c in chunks_a if law_a in c.law_name] or chunks_a[:3]
    filtered_b = [c for c in chunks_b if law_b in c.law_name] or chunks_b[:3]

    result = {
        "topic": topic,
        "law_a": {
            "name": law_a,
            "articles": [
                {"article_no": c.article_no, "hierarchy": c.hierarchy, "content": c.content}
                for c in filtered_a
            ],
        },
        "law_b": {
            "name": law_b,
            "articles": [
                {"article_no": c.article_no, "hierarchy": c.hierarchy, "content": c.content}
                for c in filtered_b
            ],
        },
    }
    return json.dumps(result, ensure_ascii=False)
