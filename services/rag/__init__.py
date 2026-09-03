"""RAG 组件入口；向量存储固定使用 Qdrant。"""
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
    "reset_vector_store",
]

_store: Optional[VectorStore] = None


def _configured_backend() -> str:
    """兼容读取配置，但只允许 Qdrant。"""
    return os.getenv("VECTOR_STORE", "qdrant").strip().lower()


def get_vector_store() -> VectorStore:
    """返回 Qdrant 向量存储单例。"""
    global _store
    if _store is not None:
        return _store

    backend = _configured_backend()
    if backend != "qdrant":
        raise ValueError(f"不支持的 VECTOR_STORE: {backend!r}；当前架构仅允许 qdrant")
    from services.rag.qdrant_store import QdrantVectorStore

    _store = QdrantVectorStore()
    return _store


def reset_vector_store() -> None:
    """重置工厂单例，主要用于测试或配置热切换。"""
    global _store
    _store = None
