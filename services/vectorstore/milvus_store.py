"""Milvus 向量存储实现（预留）。

本模块为 Milvus 分布式向量数据库的接口骨架，
当前版本仅定义接口结构，实际功能待后续版本实现。
通过 VECTORSTORE_TYPE=milvus 环境变量激活。
"""
from __future__ import annotations

from services.vectorstore.base import LawChunk, LawVectorStore


class MilvusLawStore:
    """基于 Milvus 的法律向量存储实现（预留骨架）。

    生产环境下使用 Milvus 可获得：
    - 分布式水平扩展能力
    - 亿级向量的高性能检索
    - 丰富的索引类型（IVF_FLAT, HNSW 等）

    当前版本未实现，调用任何方法将抛出 NotImplementedError。
    """

    def __init__(self, uri: str | None = None):
        """
        函数作用：
            初始化 Milvus 连接（预留）。
        输入参数：
            - uri: str | None，默认值 None
        输出参数：
            - 未标注
        """
        raise NotImplementedError(
            "Milvus 实现尚未完成，请使用 VECTORSTORE_TYPE=chroma。"
            "如需 Milvus 支持，请参考 pymilvus 文档完成本模块实现。"
        )

    def search(
        self, query_embedding: list[float], top_k: int = 20
    ) -> list[tuple[LawChunk, float]]:
        """
        函数作用：
            待补充。
        输入参数：
            - query_embedding: list[float]
            - top_k: int，默认值 20
        输出参数：
            - list[tuple[LawChunk, float]]
        """
        raise NotImplementedError

    def add_chunks(
        self, chunks: list[LawChunk], embeddings: list[list[float]]
    ) -> None:
        """
        函数作用：
            待补充。
        输入参数：
            - chunks: list[LawChunk]
            - embeddings: list[list[float]]
        输出参数：
            - 无
        """
        raise NotImplementedError

    def count(self) -> int:
        """
        函数作用：
            待补充。
        输入参数：
            - 无
        输出参数：
            - int
        """
        raise NotImplementedError

    def clear(self) -> None:
        """
        函数作用：
            待补充。
        输入参数：
            - 无
        输出参数：
            - 无
        """
        raise NotImplementedError
