"""RAG 数据模型与可插拔向量存储接口。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable


@dataclass
class LawChunk:
    """法律语料中可独立检索的最小文档单元。"""

    law_name: str
    hierarchy: str
    article_no: str
    content: str
    chunk_id: str
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class VectorStore(Protocol):
    """向量数据库统一接口，上层 RAG 不依赖具体后端。"""

    def add_documents(
        self,
        documents: list[LawChunk],
        embeddings: list[list[float]],
    ) -> None:
        """批量新增或更新文档及其向量。"""
        ...

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 20,
    ) -> list[tuple[LawChunk, float]]:
        """按向量相似度返回文档与分数。"""
        ...

    def delete(self, document_ids: Sequence[str] | None = None) -> None:
        """删除指定文档；未提供 ID 时清空当前 collection。"""
        ...

    def health_check(self) -> bool:
        """检查后端是否可访问。"""
        ...

    def count(self) -> int:
        """返回当前 collection 的文档数。"""
        ...


@runtime_checkable
class LawRetriever(Protocol):
    """法律检索器统一接口。"""

    def retrieve(self, query: str, top_k: int = 5) -> list[LawChunk]:
        """根据自然语言查询返回相关法律文档。"""
        ...


# 兼容旧命名，供渐进迁移期间的既有调用使用。
LawVectorStore = VectorStore
