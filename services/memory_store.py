"""长期记忆向量存储 —— 基于 ChromaDB 的语义记忆检索。

职责：
- 将结构化记忆条目（语义记忆/情节记忆/程序记忆）存入 ChromaDB
- 支持语义检索 + 新鲜度权重的综合排序
- 复用现有 ChromaDB 实例（data/chroma_db/），使用独立 collection "memory"

存储粒度：一次完整交互 或 一个独立知识点（不是逐条消息）。
"""
from __future__ import annotations

import math
import os
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ─── 新鲜度衰减配置 ──────────────────────────────────────────────────────
FRESHNESS_WEIGHT = 0.3       # 新鲜度在最终得分中的权重
SEMANTIC_WEIGHT = 0.7        # 语义相似度在最终得分中的权重
DECAY_RATE = 0.05            # 时间衰减系数（越大衰减越快）


@dataclass
class MemoryItem:
    """长期记忆条目。"""
    content: str                          # 记忆内容
    memory_type: str                      # 类型：semantic / episodic / procedural
    thread_id: str                        # 来源对话
    created_at: int                       # 创建时间戳
    metadata: dict = field(default_factory=dict)  # 附加元数据
    score: float = 0.0                    # 检索得分（综合语义 + 新鲜度）


class MemoryStore:
    """长期记忆向量存储，基于 ChromaDB memory collection。

    使用与法条索引相同的 ChromaDB 实例，但独立 collection。
    embedding 模型复用 bge-small-zh-v1.5。
    """

    def __init__(self):
        """
        函数作用：
            初始化 ChromaDB memory collection 和 embedding 模型。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        import chromadb
        from chromadb.config import Settings

        db_path = os.getenv("CHROMA_DB_PATH", "data/chroma_db")
        self._client = chromadb.PersistentClient(
            path=db_path,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name="memory",
            metadata={"hnsw:space": "cosine"},
        )
        self._model = None
        log.info(
            "长期记忆向量存储初始化完成，当前记忆条目数: %d",
            self._collection.count(),
        )

    def _get_model(self):
        """
        函数作用：
            延迟加载 embedding 模型（复用 bge-small-zh-v1.5）。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            model_name = os.getenv("EMBEDDING_MODEL", "models/bge-small-zh-v1.5")
            model_path = Path(model_name)
            if model_path.exists():
                model_name = str(model_path.resolve())
            self._model = SentenceTransformer(model_name)
        return self._model

    def _embed(self, text: str) -> list[float]:
        """
        函数作用：
            将文本编码为向量。
        输入参数：
            - text: str
        输出参数：
            - list[float]
        """
        model = self._get_model()
        return model.encode(text, normalize_embeddings=True).tolist()

    def add_memory(
        self,
        thread_id: str,
        content: str,
        memory_type: str,
        metadata: Optional[dict] = None,
    ) -> str:
        """
        函数作用：
            添加一条长期记忆。
        输入参数：
            - thread_id: str
            - content: str
            - memory_type: str
            - metadata: Optional[dict]，默认值 None
        输出参数：
            - str
        """
        now = int(time.time())
        memory_id = f"mem_{thread_id}_{now}_{hash(content) % 10000:04d}"
        embedding = self._embed(content)

        meta = {
            "thread_id": thread_id,
            "memory_type": memory_type,
            "created_at": now,
            **(metadata or {}),
        }

        self._collection.upsert(
            ids=[memory_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[meta],
        )
        log.debug("长期记忆已存储: type=%s, id=%s", memory_type, memory_id)
        return memory_id

    def search_memories(
        self,
        query: str,
        thread_id: Optional[str] = None,
        top_k: int = 5,
    ) -> list[MemoryItem]:
        """
        函数作用：
            检索相关长期记忆，应用新鲜度权重重排序。
        输入参数：
            - query: str
            - thread_id: Optional[str]，默认值 None
            - top_k: int，默认值 5
        输出参数：
            - list[MemoryItem]
        """
        if self._collection.count() == 0:
            return []

        query_embedding = self._embed(query)

        # ChromaDB 检索（取 top_k * 3 候选，留余量给新鲜度重排）
        where_filter = {"thread_id": thread_id} if thread_id else None
        n_results = min(top_k * 3, self._collection.count())

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        if not results["ids"][0]:
            return []

        # 组装结果并应用新鲜度权重
        now = time.time()
        items: list[MemoryItem] = []

        for i, doc_id in enumerate(results["ids"][0]):
            content = results["documents"][0][i]
            meta = results["metadatas"][0][i]
            distance = results["distances"][0][i]

            # ChromaDB cosine distance → similarity
            semantic_score = 1.0 - distance

            # 新鲜度得分：指数衰减
            created_at = meta.get("created_at", 0)
            days_elapsed = (now - created_at) / 86400.0
            freshness_score = math.exp(-DECAY_RATE * days_elapsed)

            # 综合得分
            final_score = SEMANTIC_WEIGHT * semantic_score + FRESHNESS_WEIGHT * freshness_score

            items.append(MemoryItem(
                content=content,
                memory_type=meta.get("memory_type", "unknown"),
                thread_id=meta.get("thread_id", ""),
                created_at=created_at,
                metadata=meta,
                score=final_score,
            ))

        # 按综合得分降序排列，取 top_k
        items.sort(key=lambda x: x.score, reverse=True)
        return items[:top_k]

    def count(self) -> int:
        """
        函数作用：
            返回当前记忆条目总数。
        输入参数：
            - 无
        输出参数：
            - int
        """
        return self._collection.count()


# ─── 模块级单例 ──────────────────────────────────────────────────────────
_memory_store: Optional[MemoryStore] = None


def init_memory_store() -> MemoryStore:
    """
    函数作用：
        初始化长期记忆向量存储（单例）。
    输入参数：
        - 无
    输出参数：
        - MemoryStore
    """
    global _memory_store
    _memory_store = MemoryStore()
    return _memory_store


def get_memory_store() -> MemoryStore:
    """
    函数作用：
        获取长期记忆向量存储实例。
    输入参数：
        - 无
    输出参数：
        - MemoryStore
    """
    if _memory_store is None:
        raise RuntimeError("memory store not initialized; call init_memory_store() first")
    return _memory_store
