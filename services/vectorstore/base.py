"""兼容层：向量存储接口已迁移到 services.rag.interfaces。"""

from services.rag.interfaces import LawChunk, LawVectorStore, VectorStore

__all__ = ["LawChunk", "LawVectorStore", "VectorStore"]
