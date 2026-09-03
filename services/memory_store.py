"""Qdrant 长期记忆向量存储。

长期记忆与法律知识分别使用独立 collection；两者共享 Qdrant 服务但不会混查。
owner/thread 过滤是强制边界，禁止无作用域的全局语义检索。
"""
from __future__ import annotations

import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

FRESHNESS_WEIGHT = 0.3
SEMANTIC_WEIGHT = 0.7
DECAY_RATE = 0.05
DEFAULT_MEMORY_COLLECTION = "legal_memory"
DEFAULT_VECTOR_SIZE = 512
_MEMORY_NAMESPACE = uuid.UUID("0b28bd33-d783-4b20-a8fd-8eae57255eae")
_PAYLOAD_INDEX_FIELDS = ("owner_id", "thread_id", "memory_type")


@dataclass
class MemoryItem:
    """长期记忆条目。"""

    content: str
    memory_type: str
    thread_id: str
    created_at: int
    metadata: dict = field(default_factory=dict)
    score: float = 0.0


class MemoryStore:
    """独立 Qdrant collection 上的长期记忆存储。"""

    def __init__(
        self,
        *,
        client: Any | None = None,
        models: Any | None = None,
        collection_name: str | None = None,
    ) -> None:
        if client is None:
            from qdrant_client import QdrantClient

            local_path = (os.getenv("QDRANT_PATH") or "").strip()
            if local_path:
                client = QdrantClient(":memory:" if local_path == ":memory:" else local_path)
            else:
                client = QdrantClient(
                    url=os.getenv("QDRANT_URL") or "http://localhost:6333",
                    api_key=os.getenv("QDRANT_API_KEY") or None,
                    timeout=int(os.getenv("QDRANT_TIMEOUT", "5")),
                )
        if models is None:
            from qdrant_client import models as qdrant_models

            models = qdrant_models
        self._client = client
        self._models = models
        self._collection_name = (
            collection_name
            or os.getenv("QDRANT_MEMORY_COLLECTION")
            or DEFAULT_MEMORY_COLLECTION
        )
        self._model = None
        self._initialized = False

    def _collection_exists(self) -> bool:
        if hasattr(self._client, "collection_exists"):
            return bool(self._client.collection_exists(self._collection_name))
        try:
            self._client.get_collection(self._collection_name)
        except Exception:
            return False
        return True

    def initialize(self, vector_size: int | None = None) -> None:
        """幂等创建长期记忆 collection 与作用域索引。"""
        size = vector_size or int(
            os.getenv("QDRANT_MEMORY_VECTOR_SIZE")
            or os.getenv("QDRANT_VECTOR_SIZE")
            or str(DEFAULT_VECTOR_SIZE)
        )
        if size <= 0:
            raise ValueError("QDRANT_MEMORY_VECTOR_SIZE 必须大于 0")
        if self._initialized and self._collection_exists():
            return
        if not self._collection_exists():
            try:
                self._client.create_collection(
                    collection_name=self._collection_name,
                    vectors_config=self._models.VectorParams(
                        size=size,
                        distance=self._models.Distance.COSINE,
                    ),
                )
            except Exception:
                if not self._collection_exists():
                    raise
        self._create_payload_indexes()
        self._initialized = True
        log.info("Qdrant 长期记忆 collection 已就绪: %s", self._collection_name)

    def _create_payload_indexes(self) -> None:
        if not hasattr(self._client, "create_payload_index"):
            return
        backend = getattr(self._client, "_client", None)
        if backend is not None and backend.__class__.__module__.startswith("qdrant_client.local"):
            return
        for field_name in _PAYLOAD_INDEX_FIELDS:
            self._client.create_payload_index(
                collection_name=self._collection_name,
                field_name=field_name,
                field_schema=self._models.PayloadSchemaType.KEYWORD,
                wait=True,
            )

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            model_name = os.getenv("EMBEDDING_MODEL", "models/bge-small-zh-v1.5")
            model_path = Path(model_name)
            if model_path.exists():
                model_name = str(model_path.resolve())
            self._model = SentenceTransformer(
                model_name,
                device=os.getenv("MODEL_DEVICE") or None,
            )
        return self._model

    def _embed(self, text: str) -> list[float]:
        return self._get_model().encode(text, normalize_embeddings=True).tolist()

    def add_memory(
        self,
        thread_id: str,
        content: str,
        memory_type: str,
        metadata: Optional[dict] = None,
        owner_id: Optional[str] = None,
    ) -> str:
        if not thread_id or not content.strip():
            raise ValueError("thread_id 和 content 不能为空")
        now = int(time.time())
        effective_owner = owner_id or thread_id
        memory_id = str(
            uuid.uuid5(
                _MEMORY_NAMESPACE,
                f"{effective_owner}\0{thread_id}\0{memory_type}\0{content}",
            )
        )
        embedding = self._embed(content)
        self.initialize(len(embedding))
        payload = {
            **(metadata or {}),
            "content": content,
            "thread_id": thread_id,
            "owner_id": effective_owner,
            "memory_type": memory_type,
            "created_at": now,
        }
        self._client.upsert(
            collection_name=self._collection_name,
            points=[self._models.PointStruct(id=memory_id, vector=embedding, payload=payload)],
            wait=True,
        )
        return memory_id

    def search_memories(
        self,
        query: str,
        thread_id: Optional[str] = None,
        top_k: int = 5,
        owner_id: Optional[str] = None,
    ) -> list[MemoryItem]:
        if top_k <= 0 or not query.strip() or not (owner_id or thread_id):
            return []
        if not self._collection_exists() or self.count() == 0:
            return []
        scope_key = "owner_id" if owner_id else "thread_id"
        scope_value = owner_id or thread_id
        query_filter = self._models.Filter(
            must=[
                self._models.FieldCondition(
                    key=scope_key,
                    match=self._models.MatchValue(value=scope_value),
                )
            ]
        )
        response = self._client.query_points(
            collection_name=self._collection_name,
            query=self._embed(query),
            query_filter=query_filter,
            limit=top_k * 3,
            with_payload=True,
            with_vectors=False,
        )
        points = getattr(response, "points", response)
        now = time.time()
        items: list[MemoryItem] = []
        for point in points:
            payload = dict(point.payload or {})
            created_at = int(payload.get("created_at") or 0)
            days_elapsed = max(0.0, (now - created_at) / 86400.0)
            freshness_score = math.exp(-DECAY_RATE * days_elapsed)
            score = SEMANTIC_WEIGHT * float(point.score) + FRESHNESS_WEIGHT * freshness_score
            items.append(
                MemoryItem(
                    content=str(payload.get("content") or ""),
                    memory_type=str(payload.get("memory_type") or "unknown"),
                    thread_id=str(payload.get("thread_id") or ""),
                    created_at=created_at,
                    metadata=payload,
                    score=score,
                )
            )
        items.sort(key=lambda item: item.score, reverse=True)
        return items[:top_k]

    def count(self) -> int:
        if not self._collection_exists():
            return 0
        return int(self._client.count(self._collection_name, exact=True).count)

    def health_check(self) -> bool:
        try:
            self._client.get_collections()
        except Exception:
            return False
        return True


_memory_store: Optional[MemoryStore] = None


def init_memory_store() -> MemoryStore:
    global _memory_store
    if _memory_store is None:
        _memory_store = MemoryStore()
    _memory_store.initialize()
    return _memory_store


def get_memory_store() -> MemoryStore:
    if _memory_store is None:
        raise RuntimeError("memory store not initialized; call init_memory_store() first")
    return _memory_store


def reset_memory_store() -> None:
    global _memory_store
    _memory_store = None


__all__ = [
    "DEFAULT_MEMORY_COLLECTION",
    "MemoryItem",
    "MemoryStore",
    "get_memory_store",
    "init_memory_store",
    "reset_memory_store",
]
