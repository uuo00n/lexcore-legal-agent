"""ChromaDB 向量存储实现。

使用 ChromaDB 作为嵌入式向量数据库，零配置、开箱即用。
数据持久化到本地目录（默认 data/chroma_db/），支持增量写入和语义检索。
"""
from __future__ import annotations

import os
from typing import Optional

import chromadb
from chromadb.config import Settings

from services.vectorstore.base import LawChunk, LawVectorStore


# ChromaDB collection 名称
_COLLECTION_NAME = "law_chunks"


class ChromaLawStore:
    """基于 ChromaDB 的法律向量存储实现。

    特性：
    - 嵌入式运行，无需外部服务
    - 数据持久化到本地磁盘
    - 支持按 metadata 过滤（法律名、条款号等）
    """

    def __init__(self, persist_dir: Optional[str] = None):
        """
        函数作用：
            初始化 ChromaDB 客户端和 collection。
        输入参数：
            - persist_dir: Optional[str]，默认值 None
        输出参数：
            - 未标注
        """
        self._persist_dir = persist_dir or os.getenv(
            "CHROMA_DB_PATH", "data/chroma_db"
        )
        self._client = chromadb.PersistentClient(
            path=self._persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},  # 使用余弦相似度
        )

    def search(
        self, query_embedding: list[float], top_k: int = 20
    ) -> list[tuple[LawChunk, float]]:
        """
        函数作用：
            根据查询向量检索最相似的法条分块。
        输入参数：
            - query_embedding: list[float]
            - top_k: int，默认值 20
        输出参数：
            - list[tuple[LawChunk, float]]
        """
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        chunks_with_scores: list[tuple[LawChunk, float]] = []

        if not results["ids"] or not results["ids"][0]:
            return chunks_with_scores

        for i, chunk_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            content = results["documents"][0][i]
            # ChromaDB 返回的 distance 是余弦距离，转换为相似度
            distance = results["distances"][0][i]
            similarity = 1.0 - distance

            chunk = LawChunk(
                law_name=meta.get("law_name", ""),
                hierarchy=meta.get("hierarchy", ""),
                article_no=meta.get("article_no", ""),
                content=content,
                chunk_id=chunk_id,
            )
            chunks_with_scores.append((chunk, similarity))

        return chunks_with_scores

    def add_chunks(
        self, chunks: list[LawChunk], embeddings: list[list[float]]
    ) -> None:
        """
        函数作用：
            批量写入法条分块及其 embedding 向量。
        输入参数：
            - chunks: list[LawChunk]
            - embeddings: list[list[float]]
        输出参数：
            - 无
        """
        batch_size = 500  # ChromaDB 推荐的批次大小

        for start in range(0, len(chunks), batch_size):
            end = min(start + batch_size, len(chunks))
            batch_chunks = chunks[start:end]
            batch_embeddings = embeddings[start:end]

            self._collection.upsert(
                ids=[c.chunk_id for c in batch_chunks],
                embeddings=batch_embeddings,
                documents=[c.content for c in batch_chunks],
                metadatas=[
                    {
                        "law_name": c.law_name,
                        "hierarchy": c.hierarchy,
                        "article_no": c.article_no,
                    }
                    for c in batch_chunks
                ],
            )

    def count(self) -> int:
        """
        函数作用：
            返回当前存储中的分块总数。
        输入参数：
            - 无
        输出参数：
            - int
        """
        return self._collection.count()

    def clear(self) -> None:
        """
        函数作用：
            清空所有已存储的数据（用于重建索引）。
        输入参数：
            - 无
        输出参数：
            - 无
        """
        self._client.delete_collection(_COLLECTION_NAME)
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )


# 类型检查：确保 ChromaLawStore 满足 LawVectorStore Protocol
def _type_check() -> None:
    """
    函数作用：
        待补充。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    store: LawVectorStore = ChromaLawStore()  # noqa: F841
