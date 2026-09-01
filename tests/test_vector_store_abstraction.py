"""VectorStore 抽象、后端选择与两种实现的回归测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.rag.interfaces import LawChunk, VectorStore


def _document(chunk_id: str = "民法典_第一条") -> LawChunk:
    return LawChunk(
        law_name="民法典",
        hierarchy="第一编 总则",
        article_no="第一条",
        content="第一条 为了保护民事主体的合法权益。",
        chunk_id=chunk_id,
        metadata={"source": "test"},
    )


class _FakeChromaCollection:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[list[float], str, dict]] = {}

    def upsert(self, *, ids, embeddings, documents, metadatas) -> None:
        for values in zip(ids, embeddings, documents, metadatas):
            chunk_id, embedding, document, metadata = values
            self.rows[chunk_id] = (embedding, document, metadata)

    def query(self, **_kwargs):
        chunk_id, (_, document, metadata) = next(iter(self.rows.items()))
        return {
            "ids": [[chunk_id]],
            "documents": [[document]],
            "metadatas": [[metadata]],
            "distances": [[0.1]],
        }

    def delete(self, *, ids) -> None:
        for chunk_id in ids:
            self.rows.pop(chunk_id, None)

    def count(self) -> int:
        return len(self.rows)


class _FakeChromaClient:
    def __init__(self) -> None:
        self.collection = _FakeChromaCollection()

    def get_or_create_collection(self, **_kwargs):
        return self.collection

    def delete_collection(self, **_kwargs) -> None:
        self.collection = _FakeChromaCollection()

    def heartbeat(self) -> int:
        return 1


def test_chroma_implements_vector_store_contract() -> None:
    from services.rag.chroma_store import ChromaVectorStore

    store = ChromaVectorStore(client=_FakeChromaClient())
    document = _document()

    assert isinstance(store, VectorStore)
    store.add_documents([document], [[1.0, 0.0]])
    matches = store.search([1.0, 0.0], top_k=1)

    assert matches[0][0] == document
    assert matches[0][1] == pytest.approx(0.9)
    assert store.health_check() is True
    store.delete([])
    assert store.count() == 1
    store.delete([document.chunk_id])
    assert store.count() == 0


class _FakeQdrantModels:
    class Distance:
        COSINE = "cosine"

    @staticmethod
    def VectorParams(**kwargs):
        return kwargs

    @staticmethod
    def PointStruct(**kwargs):
        return SimpleNamespace(**kwargs)


class _FakeQdrantClient:
    def __init__(self) -> None:
        self.exists = False
        self.points = {}

    def collection_exists(self, _name) -> bool:
        return self.exists

    def create_collection(self, **_kwargs) -> None:
        self.exists = True

    def upsert(self, *, points, **_kwargs) -> None:
        self.points.update({point.id: point for point in points})

    def query_points(self, **_kwargs):
        points = [
            SimpleNamespace(
                id=point.id,
                payload=point.payload,
                score=0.95,
            )
            for point in self.points.values()
        ]
        return SimpleNamespace(points=points)

    def delete(self, *, points_selector, **_kwargs) -> None:
        for point_id in points_selector:
            self.points.pop(point_id, None)

    def delete_collection(self, _name) -> None:
        self.exists = False
        self.points.clear()

    def get_collections(self):
        return []

    def count(self, _name, exact=True):
        return SimpleNamespace(count=len(self.points), exact=exact)


def test_qdrant_implements_vector_store_contract_and_preserves_business_id() -> None:
    from services.rag.qdrant_store import QdrantVectorStore

    client = _FakeQdrantClient()
    store = QdrantVectorStore(client=client, models=_FakeQdrantModels)
    document = _document("带中文的业务_ID")

    assert isinstance(store, VectorStore)
    store.add_documents([document], [[1.0, 0.0]])
    matches = store.search([1.0, 0.0], top_k=1)

    assert matches == [(document, 0.95)]
    assert next(iter(client.points)) != document.chunk_id
    assert store.health_check() is True
    store.delete([])
    assert store.count() == 1
    store.delete([document.chunk_id])
    assert store.count() == 0


def test_qdrant_in_memory_round_trip() -> None:
    from qdrant_client import QdrantClient, models

    from services.rag.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(
        client=QdrantClient(":memory:"),
        models=models,
        collection_name="test_round_trip",
    )
    document = _document("中文_ID")

    store.add_documents([document], [[1.0, 0.0]])
    matches = store.search([1.0, 0.0], top_k=1)

    assert matches[0][0] == document
    assert matches[0][1] == pytest.approx(1.0)
    store.delete()
    assert store.count() == 0


def test_factory_defaults_to_chroma_and_supports_qdrant(monkeypatch) -> None:
    import services.rag as rag
    import services.rag.chroma_store as chroma_module
    import services.rag.qdrant_store as qdrant_module

    chroma = object()
    qdrant = object()
    monkeypatch.delenv("VECTOR_STORE", raising=False)
    monkeypatch.delenv("VECTORSTORE_TYPE", raising=False)
    monkeypatch.setattr(chroma_module, "ChromaVectorStore", lambda: chroma)
    rag.reset_vector_store()
    assert rag.get_vector_store() is chroma

    monkeypatch.setenv("VECTOR_STORE", "qdrant")
    monkeypatch.setattr(qdrant_module, "QdrantVectorStore", lambda: qdrant)
    rag.reset_vector_store()
    assert rag.get_vector_store() is qdrant


def test_factory_rejects_unknown_backend(monkeypatch) -> None:
    import services.rag as rag

    monkeypatch.setenv("VECTOR_STORE", "unknown")
    rag.reset_vector_store()
    with pytest.raises(ValueError, match="chroma, qdrant"):
        rag.get_vector_store()
