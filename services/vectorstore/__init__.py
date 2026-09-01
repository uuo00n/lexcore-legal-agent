"""兼容层：新代码请从 services.rag 导入。"""

from services.rag import (
    DocumentResult,
    LawChunk,
    MetadataFilter,
    VectorStore,
    get_vector_store,
    get_vectorstore,
    reset_store,
    reset_vector_store,
)

LawVectorStore = VectorStore

__all__ = [
    "LawChunk",
    "LawVectorStore",
    "DocumentResult",
    "MetadataFilter",
    "VectorStore",
    "get_vector_store",
    "get_vectorstore",
    "reset_store",
    "reset_vector_store",
]
