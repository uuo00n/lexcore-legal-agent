"""Rerank 精排器 —— 使用 Cross-Encoder 对候选结果重新排序。

Cross-Encoder 直接对 (query, document) 对进行相关性打分，
精度远高于 Bi-Encoder（embedding 相似度），但速度较慢，
因此仅用于对粗排后的少量候选（top_20）进行精排。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from services.vectorstore.base import LawChunk


# 模块级单例
_reranker_model = None


def _get_reranker():
    """
    函数作用：
        延迟加载 reranker 模型（单例）。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    global _reranker_model
    if _reranker_model is None:
        from sentence_transformers import CrossEncoder

        model_name = os.getenv("RERANKER_MODEL", "models/bge-reranker-base")
        model_path = Path(model_name)
        if model_path.exists():
            model_name = str(model_path.resolve())
        _reranker_model = CrossEncoder(model_name)
    return _reranker_model


class Reranker:
    """Cross-Encoder 精排器。

    对粗排阶段产出的候选法条进行精细化重排序，
    输出最终的 top_n 高质量结果。
    """

    def __init__(self, top_n: Optional[int] = None):
        """
        函数作用：
            初始化精排器。
        输入参数：
            - top_n: Optional[int]，默认值 None
        输出参数：
            - 未标注
        """
        self._top_n = top_n or int(os.getenv("RERANKER_TOP_N", "5"))

    def rerank(
        self, query: str, chunks: list[LawChunk], top_n: Optional[int] = None
    ) -> list[tuple[LawChunk, float]]:
        """
        函数作用：
            对候选法条进行精排。
        输入参数：
            - query: str
            - chunks: list[LawChunk]
            - top_n: Optional[int]，默认值 None
        输出参数：
            - list[tuple[LawChunk, float]]
        """
        if not chunks:
            return []

        n = top_n or self._top_n
        reranker = _get_reranker()

        # 构造 (query, document) 对
        pairs = [(query, chunk.content) for chunk in chunks]

        # Cross-Encoder 打分
        scores = reranker.predict(pairs)

        # 按分数降序排列
        scored_chunks = sorted(
            zip(chunks, scores), key=lambda x: x[1], reverse=True
        )

        return [(chunk, float(score)) for chunk, score in scored_chunks[:n]]
