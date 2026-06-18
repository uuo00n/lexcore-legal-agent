"""检索层工厂 —— 初始化和管理检索器单例。

提供 init_retriever() 和 get_retriever() 两个入口：
- init_retriever(): 应用启动时调用，加载模型 + 构建 BM25 索引
- get_retriever(): 运行时获取已初始化的检索器实例
"""
from __future__ import annotations

import logging
from typing import Optional

from services.retriever.base import LawRetriever
from services.retriever.hybrid import HybridRetriever
from services.retriever.keyword import KeywordRetriever
from services.retriever.semantic import SemanticRetriever
from services.retriever.reranker import Reranker
from services.vectorstore.base import LawChunk

__all__ = ["init_retriever", "get_retriever", "LawRetriever", "LawChunk"]

log = logging.getLogger("legal.retriever")

# 模块级单例
_retriever: Optional[HybridRetriever] = None


def init_retriever(chunks: Optional[list[LawChunk]] = None) -> HybridRetriever:
    """
    函数作用：
        初始化混合检索器（应用启动时调用一次）。
    输入参数：
        - chunks: Optional[list[LawChunk]]，默认值 None
    输出参数：
        - HybridRetriever
    """
    global _retriever

    log.info("初始化混合检索器...")

    # 构建各组件
    semantic = SemanticRetriever()
    reranker = Reranker()

    keyword: Optional[KeywordRetriever] = None
    if chunks:
        log.info("构建 BM25 索引（%d 个 chunk）...", len(chunks))
        keyword = KeywordRetriever(chunks)

    _retriever = HybridRetriever(
        semantic=semantic,
        keyword=keyword,
        reranker=reranker,
    )

    log.info("混合检索器初始化完成")
    return _retriever


def get_retriever() -> HybridRetriever:
    """
    函数作用：
        获取已初始化的检索器单例。
    输入参数：
        - 无
    输出参数：
        - HybridRetriever
    """
    if _retriever is None:
        raise RuntimeError(
            "检索器尚未初始化，请先调用 init_retriever()。"
            "通常在 FastAPI lifespan 中完成初始化。"
        )
    return _retriever


def reset_retriever() -> None:
    """
    函数作用：
        重置单例（仅供测试使用）。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    global _retriever
    _retriever = None
