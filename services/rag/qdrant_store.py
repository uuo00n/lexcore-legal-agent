"""Qdrant 的 VectorStore 实现。"""
from __future__ import annotations

import os
import uuid
from typing import Any, Sequence

from services.rag.interfaces import LawChunk

DEFAULT_COLLECTION = "law_chunks"
_ID_NAMESPACE = uuid.UUID("e7669b1e-266e-4f5e-999d-56ab838c201e")


class QdrantVectorStore:
    """支持远程 Qdrant，也支持本地/内存客户端用于开发测试。"""

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        collection_name: str | None = None,
        client: Any | None = None,
        models: Any | None = None,
    ) -> None:
        if client is None:
            from qdrant_client import QdrantClient

            local_path = os.getenv("QDRANT_PATH")
            if local_path:
                client = (
                    QdrantClient(":memory:")
                    if local_path == ":memory:"
                    else QdrantClient(path=local_path)
                )
            else:
                client = QdrantClient(
                    url=url or os.getenv("QDRANT_URL", "http://localhost:6333"),
                    api_key=api_key or os.getenv("QDRANT_API_KEY"),
                    timeout=int(os.getenv("QDRANT_TIMEOUT", "5")),
                )
        if models is None:
            from qdrant_client import models as qdrant_models

            models = qdrant_models
        self._client = client
        self._models = models
        self._collection_name = collection_name or os.getenv(
            "QDRANT_COLLECTION", DEFAULT_COLLECTION
        )

    @staticmethod
    def _point_id(document_id: str) -> str:
        """把任意业务 ID 稳定映射为 Qdrant 支持的 UUID。"""
        return str(uuid.uuid5(_ID_NAMESPACE, document_id))

    def _collection_exists(self) -> bool:
        if hasattr(self._client, "collection_exists"):
            return bool(self._client.collection_exists(self._collection_name))
        try:
            self._client.get_collection(self._collection_name)
        except Exception:
            return False
        return True

    def _ensure_collection(self, vector_size: int) -> None:
        if self._collection_exists():
            return
        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=self._models.VectorParams(
                size=vector_size,
                distance=self._models.Distance.COSINE,
            ),
        )

    def add_documents(
        self,
        documents: list[LawChunk],
        embeddings: list[list[float]],
    ) -> None:
        if len(documents) != len(embeddings):
            raise ValueError("documents 与 embeddings 数量必须一致")
        if not documents:
            return
        if not embeddings[0]:
            raise ValueError("embedding 不能为空")
        vector_size = len(embeddings[0])
        if any(len(vector) != vector_size for vector in embeddings):
            raise ValueError("同一批 embeddings 的维度必须一致")
        self._ensure_collection(vector_size)

        points = [
            self._models.PointStruct(
                id=self._point_id(document.chunk_id),
                vector=embedding,
                payload={
                    "chunk_id": document.chunk_id,
                    "law_name": document.law_name,
                    "hierarchy": document.hierarchy,
                    "article_no": document.article_no,
                    "content": document.content,
                    "metadata": document.metadata,
                },
            )
            for document, embedding in zip(documents, embeddings)
        ]
        self._client.upsert(
            collection_name=self._collection_name,
            points=points,
            wait=True,
        )

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 20,
    ) -> list[tuple[LawChunk, float]]:
        if top_k <= 0 or not self._collection_exists():
            return []
        response = self._client.query_points(
            collection_name=self._collection_name,
            query=query_embedding,
            limit=top_k,
            with_payload=True,
            with_vectors=False,
        )
        points = getattr(response, "points", response)
        matches: list[tuple[LawChunk, float]] = []
        for point in points:
            payload = point.payload or {}
            document = LawChunk(
                law_name=payload.get("law_name", ""),
                hierarchy=payload.get("hierarchy", ""),
                article_no=payload.get("article_no", ""),
                content=payload.get("content", ""),
                chunk_id=payload.get("chunk_id", str(point.id)),
                metadata=payload.get("metadata") or {},
            )
            matches.append((document, float(point.score)))
        return matches

    def delete(self, document_ids: Sequence[str] | None = None) -> None:
        if not self._collection_exists():
            return
        if document_ids is not None:
            if not document_ids:
                return
            self._client.delete(
                collection_name=self._collection_name,
                points_selector=[self._point_id(value) for value in document_ids],
                wait=True,
            )
            return
        self._client.delete_collection(self._collection_name)

    def health_check(self) -> bool:
        try:
            self._client.get_collections()
        except Exception:
            return False
        return True

    def count(self) -> int:
        if not self._collection_exists():
            return 0
        return int(self._client.count(self._collection_name, exact=True).count)

    # 迁移期兼容旧接口。
    def add_chunks(
        self,
        chunks: list[LawChunk],
        embeddings: list[list[float]],
    ) -> None:
        self.add_documents(chunks, embeddings)

    def clear(self) -> None:
        self.delete()


QdrantLawStore = QdrantVectorStore
