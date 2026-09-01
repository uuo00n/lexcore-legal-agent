"""兼容层：BM25 已迁移到 services.rag.bm25。"""

from services.rag.bm25 import BM25Retriever, KeywordRetriever, _tokenize

__all__ = ["BM25Retriever", "KeywordRetriever", "_tokenize"]
