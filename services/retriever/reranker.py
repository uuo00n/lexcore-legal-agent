"""兼容层：精排器已迁移到 services.rag.reranker。"""

from services.rag.reranker import Reranker, _get_reranker

__all__ = ["Reranker", "_get_reranker"]
