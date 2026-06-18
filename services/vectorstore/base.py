"""向量存储层 —— 数据模型与抽象接口定义。

本模块定义了法律文本分块的数据结构（LawChunk）和向量存储的 Protocol 接口，
所有具体实现（Chroma、Milvus）都必须遵循此接口，确保上层模块与底层存储解耦。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class LawChunk:
    """法律条款分块 —— 向量检索的最小单元。

    Attributes:
        law_name: 法律名称，如"民法典"
        hierarchy: 层级路径，如"第三编 合同 > 第一章 一般规定"
        article_no: 条款号，如"第四百九十条"
        content: 条款正文内容
        chunk_id: 全局唯一标识，格式为 "{法律名}_{条款号}"
        metadata: 扩展元数据，预留给未来版本使用
    """

    law_name: str
    hierarchy: str
    article_no: str
    content: str
    chunk_id: str
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class LawVectorStore(Protocol):
    """向量存储抽象接口。

    所有向量数据库实现（ChromaDB、Milvus 等）必须实现此 Protocol，
    上层检索模块仅依赖此接口，不直接引用具体实现类。
    """

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
        ...

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
        ...

    def count(self) -> int:
        """
        函数作用：
            返回当前存储中的分块总数。
        输入参数：
            - 无
        输出参数：
            - int
        """
        ...

    def clear(self) -> None:
        """
        函数作用：
            清空所有已存储的数据（用于重建索引）。
        输入参数：
            - 无
        输出参数：
            - 无
        """
        ...
