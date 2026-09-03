"""RAG 检索编排：向量召回、BM25、RRF 融合与精排。"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from services.cache.retrieval import get_cached_results, set_cached_results
from services.errors import RetrievalError
from services.retry import is_retryable_exception, retry_sync
from services.rag import get_vector_store
from services.rag.bm25 import BM25Retriever
from services.rag.fusion import (
    append_unique_results,
    reciprocal_rank_fusion,
    reciprocal_rank_fusion_scored,
)
from services.rag.interfaces import (
    DocumentResult,
    LawChunk,
    MetadataFilter,
    VectorStore,
    is_superseded,
)
from services.rag.reranker import Reranker

log = logging.getLogger("legal.retriever")
_embedding_model = None
_retriever: Optional["HybridRetriever"] = None
_DEFAULT_RERANKER = object()

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
        model_device = os.getenv("MODEL_DEVICE") or None
        _embedding_model = SentenceTransformer(model_name, device=model_device)
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


def _positive_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None and name in {
        "RETRIEVAL_VECTOR_TOP_K",
        "RETRIEVAL_BM25_TOP_K",
    }:
        raw_value = os.getenv("RETRIEVER_TOP_K")
    value = int(raw_value if raw_value is not None else default)
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _serialize_hits(results: list[DocumentResult]) -> list[dict[str, Any]]:
    return [
        {
            "rank": rank,
            "chunk_id": document.chunk_id,
            "law_name": document.law_name,
            "article_no": document.article_no,
            "score": float(score),
        }
        for rank, (document, score) in enumerate(results, start=1)
    ]


def _record_retrieval_event(
    trace_id: str | None,
    event_type: str,
    results: list[DocumentResult],
    **details: Any,
) -> None:
    """记录检索阶段命中；可观测性故障不得影响检索主链路。"""
    if not trace_id:
        return
    try:
        from services.observability import record_event

        record_event(
            trace_id,
            event_type,
            name="hybrid_retrieval",
            payload={"hits": _serialize_hits(results), **details},
        )
    except Exception as exc:
        log.debug("retrieval trace event skipped: %s", exc)


def _record_rag_summary(trace_id: str | None, **payload: Any) -> None:
    """记录 RAG 汇总指标；未初始化观测存储时不得影响独立检索调用。"""
    if not trace_id:
        return
    try:
        from services.observability import record_event

        record_event(
            trace_id,
            "rag_retrieval",
            name="hybrid_retrieval",
            payload=payload,
        )
    except Exception as exc:
        log.debug("rag summary trace skipped: %s", exc)


class HybridRetriever:
    """语义召回与 BM25 召回经 RRF 融合后再精排。"""

    def __init__(
        self,
        semantic: Optional[SemanticRetriever] = None,
        keyword: Optional[BM25Retriever] = None,
        reranker: Reranker | None | object = _DEFAULT_RERANKER,
        rrf_k: Optional[int] = None,
        vector_top_k: Optional[int] = None,
        bm25_top_k: Optional[int] = None,
        final_top_k: Optional[int] = None,
        # 兼容旧构造参数；设置后同时覆盖两路召回 TopK。
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
        min_results: Optional[int] = None,
        include_superseded: Optional[bool] = None,
    ) -> None:
        # init_retriever 会注入正式向量检索器；延迟创建便于独立测试 RRF。
        self._semantic = semantic
        self._keyword = keyword
        self._reranker = Reranker() if reranker is _DEFAULT_RERANKER else reranker
        self._rrf_k = rrf_k if rrf_k is not None else int(os.getenv("RRF_K", "60"))
        legacy_top_k = top_k
        self._vector_top_k = (
            vector_top_k
            if vector_top_k is not None
            else legacy_top_k
            if legacy_top_k is not None
            else _positive_int_env("RETRIEVAL_VECTOR_TOP_K", 10)
        )
        self._bm25_top_k = (
            bm25_top_k
            if bm25_top_k is not None
            else legacy_top_k
            if legacy_top_k is not None
            else _positive_int_env("RETRIEVAL_BM25_TOP_K", 10)
        )
        self._final_top_k = (
            final_top_k
            if final_top_k is not None
            else _positive_int_env("RETRIEVAL_FINAL_TOP_K", 5)
        )
        for name, value in (
            ("vector_top_k", self._vector_top_k),
            ("bm25_top_k", self._bm25_top_k),
            ("final_top_k", self._final_top_k),
        ):
            if value <= 0:
                raise ValueError(f"{name} 必须大于 0")
        self._top_k = max(self._vector_top_k, self._bm25_top_k)
        self._score_threshold = (
            score_threshold
            if score_threshold is not None
            else float(os.getenv("RERANKER_SCORE_THRESHOLD", "0.3"))
        )
        # 阈值只用于「削掉尾部弱相关」，不允许把结果削成空。低于阈值时至少
        # 保留最高分的若干条，由上层的 low_quality / evidence_insufficient
        # 标记去表达「不可信」，否则调用方无法区分「库里没有」和「分数不够」。
        self._min_results = (
            min_results
            if min_results is not None
            else _positive_int_env("RETRIEVAL_MIN_RESULTS", 1)
        )
        # 历史版本 / 已废止条文默认不参与召回：本地语料里它们与现行法条几乎
        # 逐字重合，会直接抢占 TopK 名额。需要查旧法时显式打开。
        self._include_superseded = (
            include_superseded
            if include_superseded is not None
            else _bool_env("RETRIEVAL_INCLUDE_SUPERSEDED", False)
        )

    @property
    def score_threshold(self) -> float:
        return self._score_threshold

    @property
    def min_results(self) -> int:
        return self._min_results

    @property
    def include_superseded(self) -> bool:
        return self._include_superseded

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
        """兼容旧测试与调用方，只返回 RRF 排序后的文档。"""
        return reciprocal_rank_fusion(
            [semantic_results, keyword_results],
            k=self._rrf_k,
        )

    _append_unique_results = staticmethod(append_unique_results)

    @staticmethod
    def _safe_retrieve(
        retriever: Any,
        query: str,
        *,
        top_k: int,
        source: str,
    ) -> tuple[list[DocumentResult], str | None]:
        try:
            def retrieve_once() -> Any:
                try:
                    return retriever.retrieve(query, top_k=top_k)
                except Exception as exc:
                    raise RetrievalError(
                        f"{source} retrieval failed.",
                        retryable=is_retryable_exception(exc),
                    ) from exc

            results = HybridRetriever._normalize_results(
                retry_sync(
                    retrieve_once,
                    operation_name=f"retrieval.{source}",
                )
            )
            return results, None
        except Exception as exc:
            log.warning("%s retrieval failed, degrading to available source: %s", source, exc)
            original = exc.__cause__ or exc
            return [], type(original).__name__

    def _retrieve_source_variants(
        self,
        retriever: Any,
        queries: list[str],
        *,
        top_k: int,
        source: str,
    ) -> tuple[list[DocumentResult], str | None]:
        combined: list[DocumentResult] = []
        error_type: str | None = None
        for candidate_query in queries:
            results, error = self._safe_retrieve(
                retriever,
                candidate_query,
                top_k=top_k,
                source=source,
            )
            if error:
                error_type = error
                break
            append_unique_results(combined, results)
        return combined[:top_k], error_type

    def _drop_superseded(
        self,
        results: list[DocumentResult],
    ) -> tuple[list[DocumentResult], int]:
        """
        函数作用：
            剔除历史版本 / 已废止条文。两路召回统一在这里过滤，因为 BM25
            是内存索引、拿不到向量库的 payload filter，只有后置过滤能保证
            两条链路口径一致。
        输入参数：
            - results: list[DocumentResult]
        输出参数：
            - tuple[list[DocumentResult], int]，(保留结果, 丢弃条数)
        """
        if self._include_superseded:
            return results, 0
        kept = [item for item in results if not is_superseded(item.document)]
        return kept, len(results) - len(kept)

    def _run_pipeline(
        self,
        query: str,
        *,
        top_k: int | None = None,
        trace_id: str | None = None,
    ) -> tuple[list[DocumentResult], bool]:
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

        semantic_queries = [hyde_document]
        if hyde_document != query:
            semantic_queries.append(query)
        if rewritten_query not in semantic_queries:
            semantic_queries.append(rewritten_query)
        try:
            semantic_retriever = self._semantic or SemanticRetriever()
            semantic_results, vector_error = self._retrieve_source_variants(
                semantic_retriever,
                semantic_queries,
                top_k=self._vector_top_k,
                source="vector",
            )
        except Exception as exc:
            log.warning("vector retrieval unavailable, degrading to BM25: %s", exc)
            semantic_results = []
            vector_error = type(exc).__name__
        _record_retrieval_event(
            trace_id,
            "vector_hits",
            semantic_results,
            top_k=self._vector_top_k,
            score_type="vector",
            error_type=vector_error,
        )

        keyword_results: list[DocumentResult] = []
        bm25_error: str | None = None
        if self._keyword:
            keyword_queries = [query]
            if rewritten_query not in keyword_queries:
                keyword_queries.append(rewritten_query)
            if hyde_document not in keyword_queries:
                keyword_queries.append(hyde_document)
            keyword_results, bm25_error = self._retrieve_source_variants(
                self._keyword,
                keyword_queries,
                top_k=self._bm25_top_k,
                source="bm25",
            )
        _record_retrieval_event(
            trace_id,
            "bm25_hits",
            keyword_results,
            top_k=self._bm25_top_k,
            score_type="bm25",
            available=self._keyword is not None,
            error_type=bm25_error,
        )

        semantic_results, superseded_vector = self._drop_superseded(semantic_results)
        keyword_results, superseded_bm25 = self._drop_superseded(keyword_results)
        if semantic_results and keyword_results:
            fused_results = reciprocal_rank_fusion_scored(
                [semantic_results, keyword_results],
                k=self._rrf_k,
            )
            fusion_mode = "rrf"
        elif semantic_results:
            fused_results = list(semantic_results)
            fusion_mode = "vector_fallback"
        else:
            fused_results = list(keyword_results)
            fusion_mode = "bm25_fallback" if keyword_results else "empty"
        _record_retrieval_event(
            trace_id,
            "fused_hits",
            fused_results,
            mode=fusion_mode,
            score_type="rrf" if fusion_mode == "rrf" else fusion_mode,
            vector_error=vector_error,
            bm25_error=bm25_error,
            superseded_dropped=superseded_vector + superseded_bm25,
        )

        final_limit = top_k if top_k is not None else self._final_top_k
        if final_limit <= 0:
            return [], False
        rerank_candidates = fused_results[
            : self._vector_top_k + self._bm25_top_k
        ]
        if self._reranker is None:
            final_results = rerank_candidates[:final_limit]
            _record_retrieval_event(
                trace_id,
                "reranker_hits",
                final_results,
                available=False,
                degraded=True,
                score_type=fusion_mode,
            )
            return final_results, False

        try:
            final_results = self._normalize_results(self._reranker.rerank(
                query,
                [document for document, _score in rerank_candidates],
                top_n=final_limit,
            ))
            _record_retrieval_event(
                trace_id,
                "reranker_hits",
                final_results,
                available=True,
                degraded=False,
                score_type="reranker",
            )
            return final_results, True
        except Exception as exc:
            log.warning("reranker failed, returning fused results: %s", exc)
            final_results = rerank_candidates[:final_limit]
            _record_retrieval_event(
                trace_id,
                "reranker_hits",
                final_results,
                available=False,
                degraded=True,
                score_type=fusion_mode,
                error_type=type(exc).__name__,
            )
            return final_results, False

    def _cache_params(self, final_limit: int) -> dict[str, Any]:
        """
        函数作用：
            汇总影响召回结果的检索参数，作为缓存 key 的一部分。
            任何一项变化都会得到不同的 key，避免调参后命中旧结果。
        输入参数：
            - final_limit: int，本次请求的最终 TopK
        输出参数：
            - dict[str, Any]
        """
        return {
            "final_top_k": final_limit,
            "vector_top_k": self._vector_top_k,
            "bm25_top_k": self._bm25_top_k,
            "rrf_k": self._rrf_k,
            "reranker": self._reranker is not None,
            "keyword": self._keyword is not None,
            "include_superseded": self._include_superseded,
        }

    def _run_pipeline_cached(
        self,
        query: str,
        *,
        top_k: int | None = None,
        trace_id: str | None = None,
    ) -> tuple[list[DocumentResult], bool]:
        """
        函数作用：
            带 Redis 缓存的检索入口。缓存未命中或 Redis 不可用时执行完整管线，
            因此 Redis 挂掉只会退化为「每次都真检索」，不影响可用性。
        输入参数：
            - query: str
            - top_k: int | None，默认值 None
            - trace_id: str | None，默认值 None
        输出参数：
            - tuple[list[DocumentResult], bool]，(结果, 是否已精排)
        """
        final_limit = top_k if top_k is not None else self._final_top_k
        params = self._cache_params(final_limit)
        started = time.perf_counter()
        cached = get_cached_results(query, params, trace_id=trace_id)
        if cached is not None:
            _record_rag_summary(
                trace_id,
                latency_ms=int((time.perf_counter() - started) * 1000),
                retrieval_count=len(cached[0]),
                cache_hit=True,
                success=True,
            )
            return cached
        try:
            results, reranked = self._run_pipeline(query, top_k=top_k, trace_id=trace_id)
        except Exception as exc:
            _record_rag_summary(
                trace_id,
                latency_ms=int((time.perf_counter() - started) * 1000),
                retrieval_count=0,
                cache_hit=False,
                success=False,
                error=str(exc),
            )
            raise
        set_cached_results(query, results, reranked=reranked, params=params)
        _record_rag_summary(
            trace_id,
            latency_ms=int((time.perf_counter() - started) * 1000),
            retrieval_count=len(results),
            cache_hit=False,
            success=True,
        )
        return results, reranked

    def retrieve_with_scores(
        self,
        query: str,
        top_k: int | None = None,
        trace_id: str | None = None,
    ) -> list[DocumentResult]:
        results, _reranked = self._run_pipeline_cached(
            query,
            top_k=top_k,
            trace_id=trace_id,
        )
        return results

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        trace_id: str | None = None,
    ) -> list[LawChunk]:
        """
        函数作用：
            返回不带分数的法条列表，并按阈值削掉尾部弱相关结果。
            与 ``retrieve_with_scores`` 的区别只在这一层过滤：调用方拿不到
            分数、无法自行判断可信度，所以这里替它裁掉尾部；但至少保留
            ``min_results`` 条，避免「候选存在却返回空」这种无法归因的结果。
        输入参数：
            - query: str
            - top_k: int | None，默认值 None
            - trace_id: str | None，默认值 None
        输出参数：
            - list[LawChunk]
        """
        results, reranked = self._run_pipeline_cached(
            query,
            top_k=top_k,
            trace_id=trace_id,
        )
        if not reranked:
            return [document for document, _score in results]
        kept = [
            document
            for document, score in results
            if score >= self._score_threshold
        ]
        if len(kept) >= self._min_results:
            return kept
        return [document for document, _score in results[: self._min_results]]


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
