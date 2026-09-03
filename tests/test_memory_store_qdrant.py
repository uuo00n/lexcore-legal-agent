"""Qdrant 长期记忆的隔离与幂等回归测试。"""
from __future__ import annotations

from types import SimpleNamespace

from services.memory_store import MemoryStore


class _Models:
    class Distance:
        COSINE = "cosine"

    class PayloadSchemaType:
        KEYWORD = "keyword"

    @staticmethod
    def VectorParams(**kwargs):
        return kwargs

    @staticmethod
    def PointStruct(**kwargs):
        return SimpleNamespace(**kwargs)

    @staticmethod
    def Filter(**kwargs):
        return SimpleNamespace(**kwargs)

    @staticmethod
    def FieldCondition(**kwargs):
        return SimpleNamespace(**kwargs)

    @staticmethod
    def MatchValue(**kwargs):
        return SimpleNamespace(**kwargs)


class _Client:
    def __init__(self):
        self.exists = False
        self.points = {}
        self.create_calls = 0
        self.indexes = []

    def collection_exists(self, _name):
        return self.exists

    def create_collection(self, **_kwargs):
        self.create_calls += 1
        self.exists = True

    def create_payload_index(self, **kwargs):
        self.indexes.append(kwargs["field_name"])

    def upsert(self, *, points, **_kwargs):
        for point in points:
            self.points[point.id] = point

    def query_points(self, *, query_filter, limit, **_kwargs):
        condition = query_filter.must[0]
        matches = [
            SimpleNamespace(payload=point.payload, score=0.9)
            for point in self.points.values()
            if point.payload.get(condition.key) == condition.match.value
        ]
        return SimpleNamespace(points=matches[:limit])

    def count(self, _name, exact=True):
        return SimpleNamespace(count=len(self.points), exact=exact)

    def get_collections(self):
        return []


class _EmbeddingModel:
    def encode(self, _text, normalize_embeddings=True):
        assert normalize_embeddings is True
        return SimpleNamespace(tolist=lambda: [1.0, 0.0])


def _store():
    client = _Client()
    store = MemoryStore(client=client, models=_Models, collection_name="test_memory")
    store._model = _EmbeddingModel()
    return store, client


def test_memory_collection_initialization_is_idempotent():
    store, client = _store()

    store.initialize(2)
    store.initialize(2)

    assert client.create_calls == 1
    assert set(client.indexes) == {"owner_id", "thread_id", "memory_type"}


def test_memory_upsert_is_stable_and_owner_scoped():
    store, client = _store()
    first = store.add_memory("thread-a", "偏好简洁回答", "semantic", owner_id="user-a")
    repeated = store.add_memory("thread-a", "偏好简洁回答", "semantic", owner_id="user-a")
    store.add_memory("thread-b", "另一个用户的隐私", "semantic", owner_id="user-b")

    assert first == repeated
    assert len(client.points) == 2
    matches = store.search_memories("回答偏好", owner_id="user-a", top_k=3)
    assert [item.content for item in matches] == ["偏好简洁回答"]


def test_memory_search_refuses_global_scope():
    store, _client = _store()
    store.add_memory("thread-a", "仅线程内可见", "episodic")

    assert store.search_memories("可见", top_k=3) == []
    assert store.search_memories("可见", thread_id="thread-a", top_k=3)[0].thread_id == "thread-a"
