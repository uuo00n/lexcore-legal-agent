"""检索质量指标计算 —— 基于 chunk_id 匹配，不需要 LLM judge。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetrievalMetrics:
    """单条查询的检索指标。"""
    hit: bool
    reciprocal_rank: float
    precision: float
    recall: float


def compute_retrieval_metrics(
    retrieved_ids: list[str],
    ground_truth_ids: list[str],
    acceptable_ids: list[str] | None = None,
) -> RetrievalMetrics:
    """
    函数作用：
        计算单条查询的检索指标。
    输入参数：
        - retrieved_ids: list[str]
        - ground_truth_ids: list[str]
        - acceptable_ids: list[str] | None，默认值 None
    输出参数：
        - RetrievalMetrics
    """
    gt_set = set(acceptable_ids or ground_truth_ids)

    if not gt_set:
        return RetrievalMetrics(hit=True, reciprocal_rank=1.0, precision=1.0, recall=1.0)

    if not retrieved_ids:
        return RetrievalMetrics(hit=False, reciprocal_rank=0.0, precision=0.0, recall=0.0)

    hit = False
    first_relevant_rank = 0

    for i, chunk_id in enumerate(retrieved_ids, start=1):
        if chunk_id in gt_set:
            if not hit:
                hit = True
                first_relevant_rank = i
            break

    reciprocal_rank = 1.0 / first_relevant_rank if first_relevant_rank > 0 else 0.0

    relevant_retrieved = sum(1 for cid in retrieved_ids if cid in gt_set)
    precision = relevant_retrieved / len(retrieved_ids) if retrieved_ids else 0.0
    recall = relevant_retrieved / len(gt_set) if gt_set else 0.0

    return RetrievalMetrics(
        hit=hit,
        reciprocal_rank=reciprocal_rank,
        precision=precision,
        recall=recall,
    )


def aggregate_metrics(results: list[RetrievalMetrics]) -> dict[str, float]:
    """
    函数作用：
        聚合多条查询的指标为平均值。
    输入参数：
        - results: list[RetrievalMetrics]
    输出参数：
        - dict[str, float]
    """
    n = len(results)
    if n == 0:
        return {"hit_rate": 0.0, "mrr": 0.0, "precision": 0.0, "recall": 0.0}

    return {
        "hit_rate": sum(r.hit for r in results) / n,
        "mrr": sum(r.reciprocal_rank for r in results) / n,
        "precision": sum(r.precision for r in results) / n,
        "recall": sum(r.recall for r in results) / n,
    }
