"""RAG 系统初始化 —— MCP Server 启动时调用，复用现有检索基础设施。"""
from __future__ import annotations

from services.rag.startup import initialize_rag as initialize_rag_service


def initialize_rag() -> None:
    """
    函数作用：
        初始化 RAG 检索系统（向量索引 + BM25 + 混合检索器）。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    initialize_rag_service()
