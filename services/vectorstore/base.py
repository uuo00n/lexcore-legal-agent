"""兼容层：向量存储接口已迁移到 services.rag.interfaces。"""

from services.rag.interfaces import (
    DocumentResult,
    LawChunk,
    LawVectorStore,
    MetadataFilter,
    VectorStore,
)

__all__ = [
    "DocumentResult",
    "LawChunk",
    "LawVectorStore",
    "MetadataFilter",
    "VectorStore",
]
