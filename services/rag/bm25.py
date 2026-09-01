"""BM25 关键词检索。"""
from __future__ import annotations

import re
from typing import Optional

from rank_bm25 import BM25Okapi

from services.rag.interfaces import LawChunk


def _tokenize(text: str) -> list[str]:
    """使用中文 unigram/bigram 与英文数字词进行轻量分词。"""
    segments = re.findall(r"[一-鿿]+|[a-zA-Z0-9]+", text)
    tokens: list[str] = []
    for segment in segments:
        if re.match(r"[a-zA-Z0-9]+", segment):
            tokens.append(segment.lower())
        else:
            tokens.extend(segment)
            tokens.extend(
                segment[index : index + 2]
                for index in range(len(segment) - 1)
            )
    return tokens


class BM25Retriever:
    """在内存中维护法律语料的 BM25 索引。"""

    def __init__(self, chunks: Optional[list[LawChunk]] = None) -> None:
        self._chunks: list[LawChunk] = []
        self._bm25: Optional[BM25Okapi] = None
        if chunks:
            self.build_index(chunks)

    def build_index(self, chunks: list[LawChunk]) -> None:
        self._chunks = chunks
        self._bm25 = BM25Okapi([_tokenize(chunk.content) for chunk in chunks])

    def retrieve(
        self,
        query: str,
        top_k: int = 20,
    ) -> list[tuple[LawChunk, float]]:
        if self._bm25 is None or not self._chunks or top_k <= 0:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(
            enumerate(scores),
            key=lambda item: item[1],
            reverse=True,
        )[:top_k]
        return [
            (self._chunks[index], float(score))
            for index, score in ranked
            if score > 0
        ]


# 旧类名兼容别名。
KeywordRetriever = BM25Retriever
