"""兼容层：语义检索已迁移到 services.rag.retriever。"""

from services.rag.retriever import SemanticRetriever, _get_model

__all__ = ["SemanticRetriever", "_get_model"]
