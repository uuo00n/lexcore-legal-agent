"""进程内 RAG 初始化，供 FastAPI 与 FastMCP 入口共同复用。"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv


log = logging.getLogger("legal.rag.startup")


def initialize_rag() -> None:
    """加载本地法律索引、BM25 语料和混合检索器。"""
    load_dotenv()
    from services.indexer.builder import load_or_build_index
    from services.indexer.chunker import chunk_all_laws
    from services.rag import get_vector_store
    from services.rag.retriever import init_retriever

    laws_dir = os.getenv("LAWS_DIR", "data/laws")
    vector_size = int(os.getenv("QDRANT_VECTOR_SIZE", "512"))
    store = get_vector_store()
    initializer = getattr(store, "initialize", None)
    if initializer is None:
        raise RuntimeError("configured vector store does not support collection initialization")
    initializer(vector_size)
    load_or_build_index(laws_dir)
    chunks = chunk_all_laws(laws_dir)
    init_retriever(chunks=chunks)
    log.info("RAG 系统初始化完成，共 %d 个法条分块", len(chunks))


__all__ = ["initialize_rag"]
