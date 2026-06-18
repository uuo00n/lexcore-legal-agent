"""语义检索器 —— 基于向量相似度的法条检索。

使用 sentence-transformers 将查询文本编码为向量，
然后在向量库中检索最相似的法条分块。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from services.vectorstore import get_vectorstore
from services.vectorstore.base import LawChunk


# 模块级单例，避免重复加载模型
_embedding_model = None


def _get_model():
    """
    函数作用：
        延迟加载 embedding 模型（单例）。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        model_name = os.getenv("EMBEDDING_MODEL", "models/bge-small-zh-v1.5")
        model_path = Path(model_name)
        if model_path.exists():
            model_name = str(model_path.resolve())
        _embedding_model = SentenceTransformer(model_name)
    return _embedding_model


class SemanticRetriever:
    """语义检索器 —— 通过向量相似度检索法条。

    工作流程：
    1. 将用户查询编码为 embedding 向量
    2. 在向量库中执行近似最近邻搜索
    3. 返回相似度最高的 top_k 个法条分块
    """

    def __init__(self, vectorstore=None):
        """
        函数作用：
            初始化语义检索器。
        输入参数：
            - vectorstore: 未标注，默认值 None
        输出参数：
            - 未标注
        """
        self._store = vectorstore or get_vectorstore()

    def retrieve(
        self, query: str, top_k: int = 20
    ) -> list[tuple[LawChunk, float]]:
        """
        函数作用：
            语义检索法条。
        输入参数：
            - query: str
            - top_k: int，默认值 20
        输出参数：
            - list[tuple[LawChunk, float]]
        """
        model = _get_model()
        # bge 模型推荐对 query 添加前缀以提升检索效果
        query_text = f"为这个句子生成表示以用于检索相关段落：{query}"
        embedding = model.encode(
            query_text, normalize_embeddings=True
        ).tolist()

        return self._store.search(embedding, top_k=top_k)
