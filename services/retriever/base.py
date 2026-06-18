"""检索层 —— 抽象接口定义。

本模块定义检索器的 Protocol 接口，所有检索实现（语义、关键词、混合）
都遵循此接口，确保检索策略可插拔替换。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from services.vectorstore.base import LawChunk


@runtime_checkable
class LawRetriever(Protocol):
    """法律检索器抽象接口。

    所有检索策略（语义检索、关键词检索、混合检索）必须实现此 Protocol，
    工具层仅依赖此接口进行法条检索。
    """

    def retrieve(self, query: str, top_k: int = 5) -> list[LawChunk]:
        """
        函数作用：
            根据查询文本检索相关法条。
        输入参数：
            - query: str
            - top_k: int，默认值 5
        输出参数：
            - list[LawChunk]
        """
        ...
