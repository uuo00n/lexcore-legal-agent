"""RAG 数据模型与可插拔向量存储接口。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping, NamedTuple, Protocol, Sequence, runtime_checkable

MetadataFilter = Mapping[str, Any]

LEGAL_PAYLOAD_FIELDS = (
    "document_id",
    "law_name",
    "law_type",
    "chapter",
    "section",
    "article",
    "paragraph",
    "item",
    "status",
    "publish_date",
    "effective_date",
    "source",
    "source_path",
    "content_hash",
)


@dataclass
class LawChunk:
    """法律语料中可独立检索的最小文档单元。"""

    law_name: str
    hierarchy: str
    article_no: str
    content: str
    chunk_id: str
    metadata: dict = field(default_factory=dict)


class DocumentResult(NamedTuple):
    """向量存储统一返回值，兼容既有 ``(document, score)`` 解包方式。"""

    document: LawChunk
    score: float


def compute_content_hash(content: str) -> str:
    """生成稳定的 SHA-256 内容指纹。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def document_payload(document: LawChunk) -> dict[str, Any]:
    """把内部法律文档展开为 Chroma/Qdrant 共用 payload。"""
    metadata = dict(document.metadata)
    payload = {
        "document_id": metadata.get("document_id") or document.chunk_id,
        "law_name": document.law_name,
        "law_type": metadata.get("law_type") or "",
        "chapter": metadata.get("chapter") or "",
        "section": metadata.get("section") or "",
        "article": metadata.get("article") or document.article_no,
        "paragraph": metadata.get("paragraph") or "",
        "item": metadata.get("item") or "",
        "status": metadata.get("status") or "",
        "publish_date": metadata.get("publish_date") or "",
        "effective_date": metadata.get("effective_date") or "",
        "source": metadata.get("source") or "",
        "source_path": metadata.get("source_path") or "",
        "content_hash": compute_content_hash(document.content),
        "content": document.content,
        "hierarchy": document.hierarchy,
        "article_no": document.article_no,
    }
    normalized_metadata = {
        key: payload[key] for key in LEGAL_PAYLOAD_FIELDS
    }
    payload["metadata"] = {**metadata, **normalized_metadata}
    return payload


def document_from_payload(payload: Mapping[str, Any]) -> LawChunk:
    """把后端 payload 还原为统一内部法律文档。"""
    metadata = dict(payload.get("metadata") or {})
    metadata.update({
        key: payload[key]
        for key in LEGAL_PAYLOAD_FIELDS
        if key in payload
    })
    return LawChunk(
        law_name=str(payload.get("law_name") or ""),
        hierarchy=str(payload.get("hierarchy") or ""),
        article_no=str(payload.get("article") or payload.get("article_no") or ""),
        content=str(payload.get("content") or ""),
        chunk_id=str(payload.get("document_id") or payload.get("chunk_id") or ""),
        metadata=metadata,
    )


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
        metadata_filter: MetadataFilter | None = None,
    ) -> list[DocumentResult]:
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
