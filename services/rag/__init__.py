"""RAG 组件入口与向量存储工厂。"""
from __future__ import annotations

import os
from typing import Optional

from services.rag.interfaces import (
    DocumentResult,
    LawChunk,
    LawRetriever,
    MetadataFilter,
    VectorStore,
)

__all__ = [
    "LawChunk",
    "LawRetriever",
    "DocumentResult",
    "MetadataFilter",
    "VectorStore",
    "get_vector_store",
    "get_vectorstore",
    "reset_vector_store",
    "reset_store",
]

_store: Optional[VectorStore] = None


def _configured_backend() -> str:
    """读取新配置，并兼容迁移前的 VECTORSTORE_TYPE。"""
    return (
        os.getenv("VECTOR_STORE")
        or os.getenv("VECTORSTORE_TYPE")
        or "chroma"
    ).strip().lower()


def get_vector_store() -> VectorStore:
    """返回按 VECTOR_STORE 配置创建的向量存储单例。"""
    global _store
    if _store is not None:
        return _store

    backend = _configured_backend()
    if backend == "chroma":
        from services.rag.chroma_store import ChromaVectorStore

        _store = ChromaVectorStore()
    elif backend == "qdrant":
        from services.rag.qdrant_store import QdrantVectorStore

        _store = QdrantVectorStore()
    else:
        raise ValueError(
            f"不支持的 VECTOR_STORE: {backend!r}，可选值: chroma, qdrant"
        )
    return _store


def reset_vector_store() -> None:
    """重置工厂单例，主要用于测试或配置热切换。"""
    global _store
    _store = None


# 旧工厂函数兼容别名。
get_vectorstore = get_vector_store
reset_store = reset_vector_store
