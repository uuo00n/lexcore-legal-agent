"""兼容层：混合检索已迁移到 services.rag.retriever。"""

from services.rag.retriever import (
    HybridRetriever,
    _arabic_to_chinese_article,
    _is_precise_legal_information_query,
    normalize_query,
)

__all__ = [
    "HybridRetriever",
    "normalize_query",
    "_arabic_to_chinese_article",
    "_is_precise_legal_information_query",
]
