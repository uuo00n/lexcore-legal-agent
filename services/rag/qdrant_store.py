"""Qdrant 的 VectorStore 实现。"""
from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from typing import Any, Sequence

from services.rag.interfaces import (
    LEGAL_PAYLOAD_FIELDS,
    DocumentResult,
    LawChunk,
    MetadataFilter,
    document_from_payload,
    document_payload,
)

DEFAULT_COLLECTION = "legal_knowledge"
_ID_NAMESPACE = uuid.UUID("e7669b1e-266e-4f5e-999d-56ab838c201e")
_PAYLOAD_INDEX_FIELDS = LEGAL_PAYLOAD_FIELDS


class QdrantVectorStore:
    """支持远程、本地磁盘和内存模式的 Qdrant 向量存储。"""

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
                    url=url
                    or os.getenv("QDRANT_URL")
                    or "http://localhost:6333",
                    api_key=api_key or os.getenv("QDRANT_API_KEY") or None,
                    timeout=int(os.getenv("QDRANT_TIMEOUT", "5")),
                )
        if models is None:
            from qdrant_client import models as qdrant_models

            models = qdrant_models
        self._client = client
        self._models = models
        self._collection_name = (
            collection_name
            or os.getenv("QDRANT_COLLECTION")
            or DEFAULT_COLLECTION
        )
        self._initialized = False

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

    def initialize(self, vector_size: int) -> None:
        """幂等初始化 collection 及常用 payload 索引。"""
        if vector_size <= 0:
            raise ValueError("vector_size 必须大于 0")
        if self._initialized and self._collection_exists():
            return

        if not self._collection_exists():
            try:
                self._client.create_collection(
                    collection_name=self._collection_name,
                    vectors_config=self._models.VectorParams(
                        size=vector_size,
                        distance=self._models.Distance.COSINE,
                    ),
                )
            except Exception:
                # 多实例并发初始化时，另一实例可能已先完成创建。
                if not self._collection_exists():
                    raise

        self._create_payload_indexes()
        self._initialized = True

    def _create_payload_indexes(self) -> None:
        if not hasattr(self._client, "create_payload_index"):
            return
        backend = getattr(self._client, "_client", None)
        if (
            backend is not None
            and backend.__class__.__module__.startswith("qdrant_client.local")
        ):
            # Qdrant local/in-memory 模式不使用 payload index。
            return
        schema = self._models.PayloadSchemaType.KEYWORD
        for field_name in _PAYLOAD_INDEX_FIELDS:
            self._client.create_payload_index(
                collection_name=self._collection_name,
                field_name=field_name,
                field_schema=schema,
                wait=True,
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
        self.initialize(vector_size)

        prepared = [
            (document_payload(document), embedding)
            for document, embedding in zip(documents, embeddings)
        ]
        incoming_hashes = {payload["content_hash"] for payload, _ in prepared}
        existing_hashes = self._existing_content_hashes(incoming_hashes)
        accepted_hashes = set(existing_hashes)
        points = []
        for payload, embedding in prepared:
            content_hash = payload["content_hash"]
            if content_hash in accepted_hashes:
                continue
            accepted_hashes.add(content_hash)
            points.append(self._models.PointStruct(
                id=self._point_id(str(payload["document_id"])),
                vector=embedding,
                payload=payload,
            ))

        batch_size = max(1, int(os.getenv("QDRANT_BATCH_SIZE", "256")))
        for start in range(0, len(points), batch_size):
            self._client.upsert(
                collection_name=self._collection_name,
                points=points[start : start + batch_size],
                wait=True,
            )

    def _existing_content_hashes(self, hashes: set[str]) -> set[str]:
        """通过 payload scroll 找出已经入库的内容指纹。"""
        if not hashes or not self._collection_exists():
            return set()

        existing: set[str] = set()
        hash_list = sorted(hashes)
        filter_batch_size = max(
            1,
            int(os.getenv("QDRANT_FILTER_BATCH_SIZE", "256")),
        )
        for start in range(0, len(hash_list), filter_batch_size):
            batch = hash_list[start : start + filter_batch_size]
            query_filter = self._build_filter({"content_hash": batch})
            offset = None
            while True:
                records, offset = self._client.scroll(
                    collection_name=self._collection_name,
                    scroll_filter=query_filter,
                    limit=filter_batch_size,
                    offset=offset,
                    with_payload=["content_hash"],
                    with_vectors=False,
                )
                existing.update(
                    str(record.payload["content_hash"])
                    for record in records
                    if record.payload and record.payload.get("content_hash")
                )
                if offset is None:
                    break
        return existing

    def _build_filter(self, metadata_filter: MetadataFilter | None):
        if not metadata_filter:
            return None
        conditions = [
            self._field_condition(key, value)
            for key, value in metadata_filter.items()
        ]
        return self._models.Filter(must=conditions)

    def _field_condition(self, key: str, value: Any):
        if isinstance(value, (list, tuple, set)):
            match = self._models.MatchAny(any=list(value))
        elif isinstance(value, Mapping):
            normalized = {str(k).lstrip("$"): v for k, v in value.items()}
            if "in" in normalized:
                match = self._models.MatchAny(any=list(normalized["in"]))
            elif "eq" in normalized:
                match = self._models.MatchValue(value=normalized["eq"])
            elif set(normalized).issubset({"gt", "gte", "lt", "lte"}):
                return self._models.FieldCondition(
                    key=key,
                    range=self._models.Range(**normalized),
                )
            else:
                raise ValueError(f"不支持的 metadata filter: {key}={value!r}")
        else:
            match = self._models.MatchValue(value=value)
        return self._models.FieldCondition(key=key, match=match)

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 20,
        metadata_filter: MetadataFilter | None = None,
    ) -> list[DocumentResult]:
        if top_k <= 0 or not self._collection_exists():
            return []
        response = self._client.query_points(
            collection_name=self._collection_name,
            query=query_embedding,
            query_filter=self._build_filter(metadata_filter),
            limit=top_k,
            with_payload=True,
            with_vectors=False,
        )
        points = getattr(response, "points", response)
        return [
            DocumentResult(
                document=document_from_payload(point.payload or {}),
                score=float(point.score),
            )
            for point in points
        ]

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
        self._initialized = False

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
