"""法律检索 MCP 工具 —— 语义 + BM25 混合检索法条。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from mcp_server.server import mcp
from services.retriever import get_retriever
from services.local_legal_retriever import LocalLegalRetriever
from services.delilegal.enums import SourceType
from services.vectorstore.base import LawChunk


def _result_item(chunk: LawChunk, score: float | None = None) -> dict:
    item = {
        "law_name": chunk.law_name,
        "article_no": chunk.article_no,
        "hierarchy": chunk.hierarchy,
        "content": chunk.content,
        "source_type": SourceType.LOCAL_RAG.value,
        "source_id": chunk.chunk_id,
        "title": chunk.law_name,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    if score is not None:
        item["rerank_score"] = round(float(score), 4)
        item["score"] = round(float(score), 4)
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
    retriever = LocalLegalRetriever(get_retriever())
    score_threshold = retriever.score_threshold
    scored_chunks = retriever.search(query, top_k=top_k)

    if not scored_chunks:
        return json.dumps(
            {
                "status": "no_relevant_result",
                "query": query,
                "score_threshold": score_threshold,
                "top_rerank_score": None,
                "results": [],
                "evidence_insufficient": True,
                "hint": "本地法库未命中。可尝试 Delilegal 法规或类案检索；"
                        "若可信数据源仍无结果，必须报告证据不足，不得编造法律依据。",
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
            "可尝试 Delilegal 法规或类案检索；若仍无结果，必须报告证据不足。"
        )
    return json.dumps(
        {
            "status": status,
            "query": query,
            "score_threshold": score_threshold,
            "top_rerank_score": round(float(top_score), 4) if top_score is not None else None,
            "results": results,
            "evidence_insufficient": is_low_quality,
            "hint": hint,
        },
        ensure_ascii=False,
    )
