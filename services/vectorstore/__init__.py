"""向量存储工厂 —— 根据配置选择具体实现。

通过环境变量 VECTORSTORE_TYPE 切换不同的向量数据库后端，
上层模块统一调用 get_vectorstore() 获取实例，无需关心底层实现。
"""
from __future__ import annotations

import os
from typing import Optional

from services.vectorstore.base import LawChunk, LawVectorStore

__all__ = ["LawChunk", "LawVectorStore", "get_vectorstore"]

# 模块级单例，避免重复初始化
_store: Optional[LawVectorStore] = None


def get_vectorstore() -> LawVectorStore:
    """
    函数作用：
        获取向量存储单例实例。
    输入参数：
        - 无
    输出参数：
        - LawVectorStore
    """
    global _store
    if _store is not None:
        return _store

    store_type = os.getenv("VECTORSTORE_TYPE", "chroma").lower()

    if store_type == "chroma":
        from services.vectorstore.chroma_store import ChromaLawStore
        _store = ChromaLawStore()
    elif store_type == "milvus":
        from services.vectorstore.milvus_store import MilvusLawStore
        _store = MilvusLawStore()
    else:
        raise ValueError(
            f"不支持的 VECTORSTORE_TYPE: {store_type!r}，可选值: chroma, milvus"
        )

    return _store


def reset_store() -> None:
    """
    函数作用：
        重置单例（仅供测试使用）。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    global _store
    _store = None
