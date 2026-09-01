"""ChromaDB 的 VectorStore 实现。"""
from __future__ import annotations

import json
import os
from typing import Any, Sequence

from services.rag.interfaces import LawChunk

DEFAULT_COLLECTION = "law_chunks"


class ChromaVectorStore:
    """保留现有本地 Chroma 持久化与余弦检索行为。"""

    def __init__(
        self,
        persist_dir: str | None = None,
        collection_name: str = DEFAULT_COLLECTION,
        client: Any | None = None,
    ) -> None:
        self._persist_dir = persist_dir or os.getenv(
            "CHROMA_DB_PATH", "data/chroma_db"
        )
        self._collection_name = collection_name
        if client is None:
            import chromadb
            from chromadb.config import Settings

            client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
        self._client = client
        self._collection = self._get_or_create_collection()

    def _get_or_create_collection(self):
        return self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
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

        batch_size = 500
        for start in range(0, len(documents), batch_size):
            batch = documents[start : start + batch_size]
            batch_embeddings = embeddings[start : start + batch_size]
            self._collection.upsert(
                ids=[document.chunk_id for document in batch],
                embeddings=batch_embeddings,
                documents=[document.content for document in batch],
                metadatas=[self._metadata(document) for document in batch],
            )

    @staticmethod
    def _metadata(document: LawChunk) -> dict[str, str]:
        return {
            "law_name": document.law_name,
            "hierarchy": document.hierarchy,
            "article_no": document.article_no,
            "metadata_json": json.dumps(
                document.metadata,
                ensure_ascii=False,
                default=str,
            ),
        }

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 20,
    ) -> list[tuple[LawChunk, float]]:
        if top_k <= 0 or self.count() == 0:
            return []
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, self.count()),
            include=["documents", "metadatas", "distances"],
        )
        if not results.get("ids") or not results["ids"][0]:
            return []

        matches: list[tuple[LawChunk, float]] = []
        for index, chunk_id in enumerate(results["ids"][0]):
            metadata = results["metadatas"][0][index] or {}
            raw_metadata = metadata.get("metadata_json", "{}")
            try:
                extra_metadata = json.loads(raw_metadata)
            except (TypeError, json.JSONDecodeError):
                extra_metadata = {}
            document = LawChunk(
                law_name=metadata.get("law_name", ""),
                hierarchy=metadata.get("hierarchy", ""),
                article_no=metadata.get("article_no", ""),
                content=results["documents"][0][index] or "",
                chunk_id=chunk_id,
                metadata=extra_metadata,
            )
            distance = float(results["distances"][0][index])
            matches.append((document, 1.0 - distance))
        return matches

    def delete(self, document_ids: Sequence[str] | None = None) -> None:
        if document_ids is not None:
            if not document_ids:
                return
            self._collection.delete(ids=list(document_ids))
            return
        try:
            self._client.delete_collection(name=self._collection_name)
        except TypeError:
            self._client.delete_collection(self._collection_name)
        self._collection = self._get_or_create_collection()

    def health_check(self) -> bool:
        try:
            self._client.heartbeat()
            self._collection.count()
        except Exception:
            return False
        return True

    def count(self) -> int:
        return int(self._collection.count())

    # 迁移期兼容旧接口。
    def add_chunks(
        self,
        chunks: list[LawChunk],
        embeddings: list[list[float]],
    ) -> None:
        self.add_documents(chunks, embeddings)

    def clear(self) -> None:
        self.delete()


ChromaLawStore = ChromaVectorStore
