"""RAG 系统初始化 —— MCP Server 启动时调用，复用现有检索基础设施。"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

log = logging.getLogger("legal.mcp")


def initialize_rag() -> None:
    """
    函数作用：
        初始化 RAG 检索系统（向量索引 + BM25 + 混合检索器）。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    load_dotenv()

    from services.indexer.builder import load_or_build_index
    from services.indexer.chunker import chunk_all_laws
    from services.retriever import init_retriever

    laws_dir = os.getenv("LAWS_DIR", "data/laws")
    load_or_build_index(laws_dir)

    chunks = chunk_all_laws(laws_dir)
    init_retriever(chunks=chunks)
    log.info("MCP: RAG 系统初始化完成，共 %d 个法条分块", len(chunks))
