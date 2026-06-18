"""关键词检索器 —— 基于 BM25 算法的法条检索。

BM25 是经典的词频-逆文档频率检索算法，擅长精确匹配关键词，
与语义检索互补：语义检索捕捉语义相似性，BM25 捕捉词汇重叠。
"""
from __future__ import annotations

import re
from typing import Optional

from rank_bm25 import BM25Okapi

from services.vectorstore.base import LawChunk


def _tokenize(text: str) -> list[str]:
    """
    函数作用：
        中文分词（简易版）。
    输入参数：
        - text: str
    输出参数：
        - list[str]
    """
    # 提取所有中文字符序列和英文/数字词
    segments = re.findall(r"[一-鿿]+|[a-zA-Z0-9]+", text)
    tokens = []
    for seg in segments:
        if re.match(r"[a-zA-Z0-9]+", seg):
            tokens.append(seg.lower())
        else:
            # 中文：unigram + bigram
            tokens.extend(list(seg))
            for i in range(len(seg) - 1):
                tokens.append(seg[i : i + 2])
    return tokens


class KeywordRetriever:
    """BM25 关键词检索器。

    在初始化时对所有法条分块建立倒排索引，
    查询时通过 BM25 算法计算相关性分数。
    """

    def __init__(self, chunks: Optional[list[LawChunk]] = None):
        """
        函数作用：
            初始化 BM25 索引。
        输入参数：
            - chunks: Optional[list[LawChunk]]，默认值 None
        输出参数：
            - 未标注
        """
        self._chunks: list[LawChunk] = []
        self._bm25: Optional[BM25Okapi] = None

        if chunks:
            self.build_index(chunks)

    def build_index(self, chunks: list[LawChunk]) -> None:
        """
        函数作用：
            构建 BM25 倒排索引。
        输入参数：
            - chunks: list[LawChunk]
        输出参数：
            - 无
        """
        self._chunks = chunks
        # 对每个 chunk 的内容进行分词
        tokenized_corpus = [_tokenize(c.content) for c in chunks]
        self._bm25 = BM25Okapi(tokenized_corpus)

    def retrieve(
        self, query: str, top_k: int = 20
    ) -> list[tuple[LawChunk, float]]:
        """
        函数作用：
            BM25 关键词检索。
        输入参数：
            - query: str
            - top_k: int，默认值 20
        输出参数：
            - list[tuple[LawChunk, float]]
        """
        if self._bm25 is None or not self._chunks:
            return []

        query_tokens = _tokenize(query)
        scores = self._bm25.get_scores(query_tokens)

        # 按分数降序排列，取 top_k
        scored_indices = sorted(
            enumerate(scores), key=lambda x: x[1], reverse=True
        )[:top_k]

        return [
            (self._chunks[idx], score)
            for idx, score in scored_indices
            if score > 0
        ]
