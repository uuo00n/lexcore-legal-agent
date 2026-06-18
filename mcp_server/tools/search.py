"""法律检索 MCP 工具 —— 语义 + BM25 混合检索法条。"""
from __future__ import annotations

import json

from mcp_server.server import mcp
from services.retriever import get_retriever
from services.vectorstore.base import LawChunk


def _result_item(chunk: LawChunk, score: float | None = None) -> dict:
    item = {
        "law_name": chunk.law_name,
        "article_no": chunk.article_no,
        "hierarchy": chunk.hierarchy,
        "content": chunk.content,
    }
    if score is not None:
        item["rerank_score"] = round(float(score), 4)
    return item


@mcp.tool()
def legal_search(query: str, top_k: int = 5) -> str:
    """
    函数作用：
        根据用户问题检索相关中国法律条款。
    输入参数：
        - query: str
        - top_k: int，默认值 5
    输出参数：
        - str
    """
    retriever = get_retriever()
    score_threshold = float(getattr(retriever, "score_threshold", 0.3))
    if hasattr(retriever, "retrieve_with_scores"):
        scored_chunks = retriever.retrieve_with_scores(query, top_k=top_k)
    else:
        scored_chunks = [(chunk, None) for chunk in retriever.retrieve(query, top_k=top_k)]

    if not scored_chunks:
        return json.dumps(
            {
                "status": "no_relevant_result",
                "query": query,
                "score_threshold": score_threshold,
                "top_rerank_score": None,
                "results": [],
                "hint": "本地法库未命中。建议：1) 用更短、更核心的法律关键词重新检索；"
                        "2) 拆分为多个小问题分别检索；"
                        "3) 如确认本地库不覆盖，调用 web_search_tool 联网搜索。"
                        "不要用相同的 query 重复检索。",
            },
            ensure_ascii=False,
        )

    top_score = scored_chunks[0][1]
    is_low_quality = top_score is not None and float(top_score) < score_threshold
    results = [_result_item(chunk, score) for chunk, score in scored_chunks]
    status = "low_quality" if is_low_quality else "found"
    hint = ""
    if is_low_quality:
        hint = (
            f"本地法库最高 rerank_score={float(top_score):.4f}，低于阈值 {score_threshold:g}。"
            "请调用 web_search_tool 联网搜索补充裁判规则或案例。"
        )
    return json.dumps(
        {
            "status": status,
            "query": query,
            "score_threshold": score_threshold,
            "top_rerank_score": round(float(top_score), 4) if top_score is not None else None,
            "results": results,
            "hint": hint,
        },
        ensure_ascii=False,
    )
