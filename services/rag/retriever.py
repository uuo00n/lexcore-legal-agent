"""RAG 检索编排：向量召回、BM25、RRF 融合与精排。"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from services.rag import get_vector_store
from services.rag.bm25 import BM25Retriever
from services.rag.fusion import append_unique_results, reciprocal_rank_fusion
from services.rag.interfaces import (
    DocumentResult,
    LawChunk,
    MetadataFilter,
    VectorStore,
)
from services.rag.reranker import Reranker

log = logging.getLogger("legal.retriever")
_embedding_model = None
_retriever: Optional["HybridRetriever"] = None

_DIGIT_MAP = {
    "0": "零", "1": "一", "2": "二", "3": "三", "4": "四",
    "5": "五", "6": "六", "7": "七", "8": "八", "9": "九",
}
_ARTICLE_NUM_RE = re.compile(r"第(\d+)条")
_PRECISE_LEGAL_INFO_RE = re.compile(
    r"(几株|多少|几克|几岁|多久|几年|什么条件|什么标准).*(犯法|违法|犯罪|判刑|处罚|拘留|赔偿|补偿|仲裁|起诉)"
    r"|"
    r"(犯法|违法|犯罪|判刑|处罚|拘留|赔偿|补偿|仲裁|起诉).*(几株|多少|几克|几岁|多久|几年|什么条件|什么标准)"
)


def _get_model():
    """延迟加载并复用 embedding 模型。"""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        model_name = os.getenv("EMBEDDING_MODEL", "models/bge-small-zh-v1.5")
        model_path = Path(model_name)
        if model_path.exists():
            model_name = str(model_path.resolve())
        _embedding_model = SentenceTransformer(model_name)
    return _embedding_model


class SemanticRetriever:
    """将文本编码后交给配置的 VectorStore 进行召回。"""

    def __init__(self, vector_store: VectorStore | None = None) -> None:
        self._store = vector_store or get_vector_store()

    def retrieve(
        self,
        query: str,
        top_k: int = 20,
        metadata_filter: MetadataFilter | None = None,
    ) -> list[DocumentResult]:
        query_text = f"为这个句子生成表示以用于检索相关段落：{query}"
        embedding = _get_model().encode(
            query_text,
            normalize_embeddings=True,
        ).tolist()
        return self._store.search(
            embedding,
            top_k=top_k,
            metadata_filter=metadata_filter,
        )


def _arabic_to_chinese_article(match: re.Match) -> str:
    num = int(match.group(1))
    if num <= 10:
        units = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
        return f"第{units[num]}条"
    if num < 20:
        return f"第十{_DIGIT_MAP[str(num % 10)]}条" if num % 10 else "第十条"
    if num < 100:
        tens = _DIGIT_MAP[str(num // 10)]
        ones = _DIGIT_MAP[str(num % 10)] if num % 10 else ""
        return f"第{tens}十{ones}条"
    if num < 1000:
        hundreds = _DIGIT_MAP[str(num // 100)]
        remainder = num % 100
        if remainder == 0:
            return f"第{hundreds}百条"
        if remainder < 10:
            return f"第{hundreds}百零{_DIGIT_MAP[str(remainder)]}条"
        tens = _DIGIT_MAP[str(remainder // 10)]
        ones = _DIGIT_MAP[str(remainder % 10)] if remainder % 10 else ""
        return f"第{hundreds}百{tens}十{ones}条"
    if num < 10000:
        thousands = _DIGIT_MAP[str(num // 1000)]
        remainder = num % 1000
        parts = [f"{thousands}千"]
        if remainder == 0:
            return f"第{''.join(parts)}条"
        if remainder < 100:
            parts.append("零")
            lower = remainder
        else:
            parts.append(f"{_DIGIT_MAP[str(remainder // 100)]}百")
            lower = remainder % 100
        if lower > 0:
            if lower < 10:
                if remainder >= 100:
                    parts.append("零")
                parts.append(_DIGIT_MAP[str(lower)])
            else:
                tens = _DIGIT_MAP[str(lower // 10)]
                ones = _DIGIT_MAP[str(lower % 10)] if lower % 10 else ""
                parts.append(f"{tens}十{ones}")
        return f"第{''.join(parts)}条"
    return match.group(0)


def normalize_query(query: str) -> str:
    """将“第N条”中的阿拉伯数字转换为中文条号。"""
    return _ARTICLE_NUM_RE.sub(_arabic_to_chinese_article, query)


def _is_precise_legal_information_query(query: str) -> bool:
    return bool(_PRECISE_LEGAL_INFO_RE.search(re.sub(r"\s+", "", query)))


class HybridRetriever:
    """语义召回与 BM25 召回经 RRF 融合后再精排。"""

    def __init__(
        self,
        semantic: Optional[SemanticRetriever] = None,
        keyword: Optional[BM25Retriever] = None,
        reranker: Optional[Reranker] = None,
        rrf_k: Optional[int] = None,
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
    ) -> None:
        self._semantic = semantic or SemanticRetriever()
        self._keyword = keyword
        self._reranker = reranker or Reranker()
        self._rrf_k = rrf_k if rrf_k is not None else int(os.getenv("RRF_K", "60"))
        self._top_k = top_k if top_k is not None else int(os.getenv("RETRIEVER_TOP_K", "20"))
        self._score_threshold = (
            score_threshold
            if score_threshold is not None
            else float(os.getenv("RERANKER_SCORE_THRESHOLD", "0.3"))
        )

    @property
    def score_threshold(self) -> float:
        return self._score_threshold

    def set_keyword_retriever(self, keyword: BM25Retriever) -> None:
        self._keyword = keyword

    @staticmethod
    def _normalize_results(results) -> list[DocumentResult]:
        return [
            result
            if isinstance(result, DocumentResult)
            else DocumentResult(result[0], float(result[1]))
            for result in results
        ]

    def _rrf_fuse(
        self,
        semantic_results: list[DocumentResult],
        keyword_results: list[DocumentResult],
    ) -> list[LawChunk]:
        return reciprocal_rank_fusion(
            [semantic_results, keyword_results],
            k=self._rrf_k,
        )

    _append_unique_results = staticmethod(append_unique_results)

    def _retrieve_scored(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[DocumentResult]:
        from services.legal_analysis import is_legal_information_query
        from services.retriever.hyde import (
            generate_hypothetical_doc,
            is_query_enhance_enabled,
            rewrite_query,
        )

        query = normalize_query(query)
        skip_enhancement = (
            is_legal_information_query(query)
            or _is_precise_legal_information_query(query)
        )
        if is_query_enhance_enabled() and not skip_enhancement:
            rewritten_query = rewrite_query(query)
            hyde_document = generate_hypothetical_doc(query)
        else:
            rewritten_query = query
            hyde_document = query

        semantic_results = self._normalize_results(
            self._semantic.retrieve(hyde_document, top_k=self._top_k)
        )
        if hyde_document != query:
            append_unique_results(
                semantic_results,
                self._normalize_results(
                    self._semantic.retrieve(query, top_k=self._top_k)
                ),
            )
        if rewritten_query not in {query, hyde_document}:
            append_unique_results(
                semantic_results,
                self._normalize_results(
                    self._semantic.retrieve(rewritten_query, top_k=self._top_k)
                ),
            )

        keyword_results: list[DocumentResult] = []
        if self._keyword:
            keyword_results = self._normalize_results(
                self._keyword.retrieve(query, top_k=self._top_k)
            )
            if rewritten_query != query:
                append_unique_results(
                    keyword_results,
                    self._normalize_results(
                        self._keyword.retrieve(rewritten_query, top_k=self._top_k)
                    ),
                )
            if hyde_document not in {query, rewritten_query}:
                append_unique_results(
                    keyword_results,
                    self._normalize_results(
                        self._keyword.retrieve(hyde_document, top_k=self._top_k)
                    ),
                )

        fused = (
            reciprocal_rank_fusion(
                [semantic_results, keyword_results],
                k=self._rrf_k,
            )
            if keyword_results
            else [document for document, _ in semantic_results]
        )
        return self._normalize_results(
            self._reranker.rerank(
                query,
                fused[: self._top_k],
                top_n=top_k,
            )
        )

    def retrieve_with_scores(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[DocumentResult]:
        return self._retrieve_scored(query, top_k=top_k)

    def retrieve(self, query: str, top_k: int = 5) -> list[LawChunk]:
        return [
            document
            for document, score in self._retrieve_scored(query, top_k=top_k)
            if score >= self._score_threshold
        ]


def init_retriever(chunks: Optional[list[LawChunk]] = None) -> HybridRetriever:
    """初始化并缓存混合检索器。"""
    global _retriever
    log.info("初始化混合检索器...")
    keyword = BM25Retriever(chunks) if chunks else None
    _retriever = HybridRetriever(
        semantic=SemanticRetriever(),
        keyword=keyword,
        reranker=Reranker(),
    )
    log.info("混合检索器初始化完成")
    return _retriever


def get_retriever() -> HybridRetriever:
    """返回已初始化的检索器。"""
    if _retriever is None:
        raise RuntimeError(
            "检索器尚未初始化，请先调用 init_retriever()。"
            "通常在 FastAPI lifespan 中完成初始化。"
        )
    return _retriever


def reset_retriever() -> None:
    """重置检索器单例。"""
    global _retriever
    _retriever = None


KeywordRetriever = BM25Retriever
