"""兼容层：RAG 检索入口已迁移到 services.rag.retriever。"""

from services.rag.interfaces import LawChunk, LawRetriever
from services.rag.retriever import (
    HybridRetriever,
    SemanticRetriever,
    get_retriever,
    init_retriever,
    reset_retriever,
)

__all__ = [
    "get_retriever",
    "init_retriever",
    "reset_retriever",
    "HybridRetriever",
    "SemanticRetriever",
    "LawRetriever",
    "LawChunk",
]
