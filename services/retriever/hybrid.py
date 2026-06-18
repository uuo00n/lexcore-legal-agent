"""混合检索器 —— 融合语义检索与关键词检索，经 RRF 粗排 + Rerank 精排。"""
from __future__ import annotations

import os
import re
from typing import Optional

from services.retriever.keyword import KeywordRetriever
from services.retriever.reranker import Reranker
from services.retriever.semantic import SemanticRetriever
from services.vectorstore.base import LawChunk

# 阿拉伯数字→中文数字映射表
_DIGIT_MAP = {"0": "零", "1": "一", "2": "二", "3": "三", "4": "四",
              "5": "五", "6": "六", "7": "七", "8": "八", "9": "九"}

_ARTICLE_NUM_RE = re.compile(r"第(\d+)条")
_PRECISE_LEGAL_INFO_RE = re.compile(
    r"(几株|多少|几克|几岁|多久|几年|什么条件|什么标准).*(犯法|违法|犯罪|判刑|处罚|拘留|赔偿|补偿|仲裁|起诉)"
    r"|"
    r"(犯法|违法|犯罪|判刑|处罚|拘留|赔偿|补偿|仲裁|起诉).*(几株|多少|几克|几岁|多久|几年|什么条件|什么标准)"
)


def _arabic_to_chinese_article(m: re.Match) -> str:
    """将 '第10条' 转为 '第十条'，支持多位数。"""
    num = int(m.group(1))
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
        h = _DIGIT_MAP[str(num // 100)]
        remainder = num % 100
        if remainder == 0:
            return f"第{h}百条"
        if remainder < 10:
            return f"第{h}百零{_DIGIT_MAP[str(remainder)]}条"
        tens = _DIGIT_MAP[str(remainder // 10)]
        ones = _DIGIT_MAP[str(remainder % 10)] if remainder % 10 else ""
        return f"第{h}百{tens}十{ones}条"
    if num < 10000:
        th = _DIGIT_MAP[str(num // 1000)]
        remainder = num % 1000
        parts = [f"{th}千"]
        if remainder == 0:
            return f"第{''.join(parts)}条"
        if remainder < 100:
            parts.append("零")
            h_remainder = remainder
        else:
            h = _DIGIT_MAP[str(remainder // 100)]
            parts.append(f"{h}百")
            h_remainder = remainder % 100
        if h_remainder > 0:
            if h_remainder < 10:
                if remainder >= 100:
                    parts.append("零")
                parts.append(_DIGIT_MAP[str(h_remainder)])
            else:
                tens = _DIGIT_MAP[str(h_remainder // 10)]
                ones = _DIGIT_MAP[str(h_remainder % 10)] if h_remainder % 10 else ""
                parts.append(f"{tens}十{ones}")
        return f"第{''.join(parts)}条"
    return m.group(0)


def normalize_query(query: str) -> str:
    """将 query 中的 '第N条' 阿拉伯数字格式转为中文数字。"""
    return _ARTICLE_NUM_RE.sub(_arabic_to_chinese_article, query)


def _is_precise_legal_information_query(query: str) -> bool:
    """
    函数作用：
        判断是否为明确法律门槛/规则查询，避免 HyDE 改写丢失关键原词。
    输入参数：
        - query: str
    输出参数：
        - bool
    """
    compact = re.sub(r"\s+", "", query)
    return bool(_PRECISE_LEGAL_INFO_RE.search(compact))


class HybridRetriever:
    """混合检索器 —— 语义 + 关键词 + RRF + Rerank。

    实现了完整的两阶段检索流水线：
    - 粗排：语义检索和关键词检索各自返回 top_k 候选，通过 RRF 融合
    - 精排：Cross-Encoder 对 RRF 结果重新排序，输出最终 top_n
    - 阈值过滤：精排分数低于阈值的结果被过滤掉
    """

    def __init__(
        self,
        semantic: Optional[SemanticRetriever] = None,
        keyword: Optional[KeywordRetriever] = None,
        reranker: Optional[Reranker] = None,
        rrf_k: Optional[int] = None,
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
    ):
        """
        函数作用：
            初始化混合检索器。
        输入参数：
            - semantic: Optional[SemanticRetriever]，默认值 None
            - keyword: Optional[KeywordRetriever]，默认值 None
            - reranker: Optional[Reranker]，默认值 None
            - rrf_k: Optional[int]，默认值 None
            - top_k: Optional[int]，默认值 None
            - score_threshold: Optional[float]，默认值 None
        输出参数：
            - 未标注
        """
        self._semantic = semantic or SemanticRetriever()
        self._keyword = keyword  # keyword 需要外部构建索引后注入
        self._reranker = reranker or Reranker()
        self._rrf_k = rrf_k or int(os.getenv("RRF_K", "60"))
        self._top_k = top_k or int(os.getenv("RETRIEVER_TOP_K", "20"))
        self._score_threshold = score_threshold or float(os.getenv("RERANKER_SCORE_THRESHOLD", "0.3"))

    @property
    def score_threshold(self) -> float:
        """
        函数作用：
            返回精排分数阈值，供工具层判断检索质量。
        输入参数：
            - 无
        输出参数：
            - float
        """
        return self._score_threshold

    def set_keyword_retriever(self, keyword: KeywordRetriever) -> None:
        """
        函数作用：
            设置关键词检索器（索引构建完成后调用）。
        输入参数：
            - keyword: KeywordRetriever
        输出参数：
            - 无
        """
        self._keyword = keyword

    def _rrf_fuse(
        self,
        semantic_results: list[tuple[LawChunk, float]],
        keyword_results: list[tuple[LawChunk, float]],
    ) -> list[LawChunk]:
        """
        函数作用：
            RRF（Reciprocal Rank Fusion）融合两路检索结果。
        输入参数：
            - semantic_results: list[tuple[LawChunk, float]]
            - keyword_results: list[tuple[LawChunk, float]]
        输出参数：
            - list[LawChunk]
        """
        k = self._rrf_k
        scores: dict[str, float] = {}  # chunk_id -> RRF 分数
        chunk_map: dict[str, LawChunk] = {}  # chunk_id -> LawChunk

        # 语义检索贡献
        for rank, (chunk, _) in enumerate(semantic_results, start=1):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0) + 1.0 / (k + rank)
            chunk_map[chunk.chunk_id] = chunk

        # 关键词检索贡献
        for rank, (chunk, _) in enumerate(keyword_results, start=1):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0) + 1.0 / (k + rank)
            chunk_map[chunk.chunk_id] = chunk

        # 按 RRF 分数降序排列
        sorted_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)
        return [chunk_map[cid] for cid in sorted_ids]

    @staticmethod
    def _append_unique_results(
        target: list[tuple[LawChunk, float]],
        additions: list[tuple[LawChunk, float]],
    ) -> None:
        """
        函数作用：
            将多路召回结果按 chunk_id 合并去重，保留先命中的排序优势。
        输入参数：
            - target: list[tuple[LawChunk, float]]
            - additions: list[tuple[LawChunk, float]]
        输出参数：
            - 无
        """
        seen = {chunk.chunk_id for chunk, _ in target}
        for chunk, score in additions:
            if chunk.chunk_id not in seen:
                target.append((chunk, score))
                seen.add(chunk.chunk_id)

    def _retrieve_scored(self, query: str, top_k: int = 5) -> list[tuple[LawChunk, float]]:
        """
        函数作用：
            执行完整的混合检索流水线并保留精排分数。
        输入参数：
            - query: str
            - top_k: int，默认值 5
        输出参数：
            - list[tuple[LawChunk, float]]
        """
        from services.retriever.hyde import (
            is_query_enhance_enabled,
            rewrite_query,
            generate_hypothetical_doc,
        )
        from services.legal_analysis import is_legal_information_query

        # 归一化：阿拉伯数字条号 → 中文数字
        query = normalize_query(query)

        # 查询增强：问题重写 + HyDE
        skip_enhancement = (
            is_legal_information_query(query)
            or _is_precise_legal_information_query(query)
        )
        if is_query_enhance_enabled() and not skip_enhancement:
            rewritten_query = rewrite_query(query)
            hyde_doc = generate_hypothetical_doc(query)
        else:
            rewritten_query = query
            hyde_doc = query

        # Step 1: 语义检索（HyDE + 原始 query + rewrite query 多路合并）
        semantic_results = self._semantic.retrieve(hyde_doc, top_k=self._top_k)
        if hyde_doc != query:
            sem_original = self._semantic.retrieve(query, top_k=self._top_k)
            self._append_unique_results(semantic_results, sem_original)
        if rewritten_query not in {query, hyde_doc}:
            sem_rewritten = self._semantic.retrieve(rewritten_query, top_k=self._top_k)
            self._append_unique_results(semantic_results, sem_rewritten)

        # Step 2: 关键词检索（原始 query + rewrite query + HyDE 文档多路合并）
        keyword_results = []
        if self._keyword:
            kw_original = self._keyword.retrieve(query, top_k=self._top_k)
            if rewritten_query != query:
                kw_rewritten = self._keyword.retrieve(rewritten_query, top_k=self._top_k)
                self._append_unique_results(kw_original, kw_rewritten)
            if hyde_doc not in {query, rewritten_query}:
                kw_hyde = self._keyword.retrieve(hyde_doc, top_k=self._top_k)
                self._append_unique_results(kw_original, kw_hyde)
            keyword_results = kw_original

        # Step 3: RRF 融合
        if keyword_results:
            fused = self._rrf_fuse(semantic_results, keyword_results)
        else:
            fused = [chunk for chunk, _ in semantic_results]

        # Step 4: Rerank 精排（用原始 query 做相关性判断）
        return self._reranker.rerank(query, fused[: self._top_k], top_n=top_k)

    def retrieve_with_scores(self, query: str, top_k: int = 5) -> list[tuple[LawChunk, float]]:
        """
        函数作用：
            返回精排后的法条和 rerank score，不按阈值过滤。
        输入参数：
            - query: str
            - top_k: int，默认值 5
        输出参数：
            - list[tuple[LawChunk, float]]
        """
        return self._retrieve_scored(query, top_k=top_k)

    def retrieve(self, query: str, top_k: int = 5) -> list[LawChunk]:
        """
        函数作用：
            执行完整的混合检索流水线。
        输入参数：
            - query: str
            - top_k: int，默认值 5
        输出参数：
            - list[LawChunk]
        """
        scored = self._retrieve_scored(query, top_k=top_k)

        # Step 5: 阈值过滤
        filtered = [chunk for chunk, score in scored if score >= self._score_threshold]

        return filtered
