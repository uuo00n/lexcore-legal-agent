"""VectorStore 抽象、后端选择与两种实现的回归测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.rag.interfaces import (
    LEGAL_PAYLOAD_FIELDS,
    DocumentResult,
    LawChunk,
    VectorStore,
)


def _document(
    chunk_id: str = "民法典_第一条",
    *,
    content: str = "第一条 为了保护民事主体的合法权益。",
    law_type: str = "法律",
) -> LawChunk:
    return LawChunk(
        law_name="民法典",
        hierarchy="第一编 总则",
        article_no="第一条",
        content=content,
        chunk_id=chunk_id,
        metadata={
            "document_id": chunk_id,
            "law_type": law_type,
            "chapter": "第一章 基本规定",
            "section": "第一节 一般规定",
            "article": "第一条",
            "paragraph": "第一款",
            "item": "第一项",
            "status": "现行有效",
            "publish_date": "2020-05-28",
            "effective_date": "2021-01-01",
            "source": "国家法律法规数据库",
            "source_path": "data/laws/02_民法典.txt",
        },
    )


class _FakeChromaCollection:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[list[float], str, dict]] = {}
        self.last_query = None

    def upsert(self, *, ids, embeddings, documents, metadatas) -> None:
        for values in zip(ids, embeddings, documents, metadatas):
            chunk_id, embedding, document, metadata = values
            self.rows[chunk_id] = (embedding, document, metadata)

    def query(self, **kwargs):
        self.last_query = kwargs
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
    matches = store.search(
        [1.0, 0.0],
        top_k=1,
        metadata_filter={"law_type": "法律"},
    )

    assert isinstance(matches[0], DocumentResult)
    assert matches[0].document.chunk_id == document.chunk_id
    assert matches[0].document.metadata["law_type"] == "法律"
    assert matches[0].score == pytest.approx(0.9)
    assert store._collection.last_query["where"] == {"law_type": "法律"}
    assert store.health_check() is True
    store.delete([])
    assert store.count() == 1
    store.delete([document.chunk_id])
    assert store.count() == 0


class _FakeQdrantModels:
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
    def MatchAny(**kwargs):
        return SimpleNamespace(**kwargs)

    @staticmethod
    def MatchValue(**kwargs):
        return SimpleNamespace(**kwargs)

    @staticmethod
    def Range(**kwargs):
        return SimpleNamespace(**kwargs)


class _FakeQdrantClient:
    def __init__(self) -> None:
        self.exists = False
        self.points = {}
        self.create_calls = 0
        self.index_calls = []
        self.upsert_calls = 0
        self.last_query_filter = None

    def collection_exists(self, _name) -> bool:
        return self.exists

    def create_collection(self, **_kwargs) -> None:
        self.create_calls += 1
        self.exists = True

    def create_payload_index(self, **kwargs) -> None:
        self.index_calls.append(kwargs["field_name"])

    def upsert(self, *, points, **_kwargs) -> None:
        self.upsert_calls += 1
        self.points.update({point.id: point for point in points})

    def query_points(self, **kwargs):
        self.last_query_filter = kwargs.get("query_filter")
        conditions = getattr(self.last_query_filter, "must", [])
        points = [
            SimpleNamespace(
                id=point.id,
                payload=point.payload,
                score=0.95,
            )
            for point in self.points.values()
            if all(self._matches(point.payload, condition) for condition in conditions)
        ]
        return SimpleNamespace(points=points)

    @staticmethod
    def _matches(payload, condition) -> bool:
        match = getattr(condition, "match", None)
        if hasattr(match, "value"):
            return payload.get(condition.key) == match.value
        if hasattr(match, "any"):
            return payload.get(condition.key) in match.any
        return True

    def scroll(self, *, scroll_filter, **_kwargs):
        conditions = getattr(scroll_filter, "must", [])
        records = [
            SimpleNamespace(id=point.id, payload=point.payload)
            for point in self.points.values()
            if all(self._matches(point.payload, condition) for condition in conditions)
        ]
        return records, None

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
    matches = store.search(
        [1.0, 0.0],
        top_k=1,
        metadata_filter={"status": "现行有效"},
    )

    assert isinstance(matches[0], DocumentResult)
    assert matches[0].document.chunk_id == document.chunk_id
    assert matches[0].score == 0.95
    assert next(iter(client.points)) != document.chunk_id
    payload = next(iter(client.points.values())).payload
    assert set(LEGAL_PAYLOAD_FIELDS).issubset(payload)
    assert payload["document_id"] == document.chunk_id
    assert payload["content_hash"]
    assert client.last_query_filter is not None
    assert store.health_check() is True
    store.delete([])
    assert store.count() == 1
    store.delete([document.chunk_id])
    assert store.count() == 0


def test_qdrant_collection_initialization_is_repeatable() -> None:
    from services.rag.qdrant_store import QdrantVectorStore

    client = _FakeQdrantClient()
    store = QdrantVectorStore(client=client, models=_FakeQdrantModels)

    store.initialize(2)
    store.initialize(2)

    assert client.create_calls == 1
    assert set(client.index_calls) == set(LEGAL_PAYLOAD_FIELDS)


def test_qdrant_ingestion_is_idempotent_by_content_hash() -> None:
    from services.rag.qdrant_store import QdrantVectorStore

    client = _FakeQdrantClient()
    store = QdrantVectorStore(client=client, models=_FakeQdrantModels)
    document = _document()

    store.add_documents([document], [[1.0, 0.0]])
    store.add_documents([document], [[1.0, 0.0]])
    store.add_documents([_document("另一个_ID")], [[1.0, 0.0]])

    assert client.upsert_calls == 1
    assert store.count() == 1

    changed = _document(document.chunk_id, content="第一条 修改后的内容。")
    store.add_documents([changed], [[0.9, 0.1]])

    assert client.upsert_calls == 2
    assert store.count() == 1


def test_qdrant_in_memory_round_trip() -> None:
    from qdrant_client import QdrantClient, models

    from services.rag.qdrant_store import QdrantVectorStore

    client = QdrantClient(":memory:")
    store = QdrantVectorStore(
        client=client,
        models=models,
        collection_name="test_round_trip",
    )
    document = _document("中文_ID")
    other = _document(
        "行政法规_ID",
        content="第二条 行政法规测试内容。",
        law_type="行政法规",
    )

    store.add_documents([document, other], [[1.0, 0.0], [0.0, 1.0]])
    store.add_documents([document], [[1.0, 0.0]])
    matches = store.search(
        [1.0, 0.0],
        top_k=2,
        metadata_filter={"law_type": "法律"},
    )

    assert len(matches) == 1
    assert isinstance(matches[0], DocumentResult)
    assert matches[0].document.chunk_id == document.chunk_id
    assert matches[0].score == pytest.approx(1.0)
    assert store.count() == 2

    second_store = QdrantVectorStore(
        client=client,
        models=models,
        collection_name="test_round_trip",
    )
    second_store.initialize(2)
    assert second_store.count() == 2

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
