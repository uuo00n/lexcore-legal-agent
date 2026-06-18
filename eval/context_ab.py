"""OpenViking Context Layer A/B 评测。

目标：
- A 组：原始 query 直接进入现有 HybridRetriever。
- B 组：先经过 OpenViking 风格 Context Layer，使用命中的 L0/URI 扩展 query。

这不是 LLM-as-judge，而是离线、可复现的检索与上下文路由评测。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from eval.metrics import RetrievalMetrics, aggregate_metrics, compute_retrieval_metrics
from services.viking_context import VikingContextHit, VikingContextResult, retrieve_viking_context


@dataclass(frozen=True)
class ExpectedContext:
    """从评测样本推断出的预期上下文目录。"""

    resource_uris: set[str] = field(default_factory=set)
    skill_uris: set[str] = field(default_factory=set)


@contextmanager
def disabled_query_enhancement():
    """临时关闭 HyDE/rewrite，用于快速 smoke test。"""
    keys = ("HYDE_ENABLED", "HYDE_REWRITE_ENABLED")
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ[key] = "false"
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _contexts_text(item: dict[str, Any]) -> str:
    values = []
    values.extend(item.get("acceptable_contexts") or [])
    values.extend(item.get("ground_truth_contexts") or [])
    values.append(item.get("question", ""))
    return " ".join(str(v) for v in values)


def infer_expected_context(item: dict[str, Any]) -> ExpectedContext:
    """从 dataset item 推断本题应命中的 Resource/Skill。

    这是评测用启发式映射，不参与生产回答。
    """
    text = _contexts_text(item)
    resources: set[str] = set()
    skills: set[str] = set()

    if any(word in text for word in ("劳动合同法", "劳动法", "劳动", "试用期", "辞退", "欠薪", "加班", "仲裁")):
        resources.add("viking://resources/laws/labor/")
        skills.add("viking://skills/legal/labor_arbitration_workflow/")

    if any(word in text for word in (
        "工资", "薪资", "劳动报酬", "欠薪", "拖欠工资", "克扣", "少发",
        "补发", "加班费", "最低工资", "试用期工资", "绩效", "奖金", "提成",
    )):
        resources.add("viking://resources/laws/labor/")
        skills.add("viking://skills/legal/wage_dispute_workflow/")

    if any(word in text for word in ("押金", "租赁", "房东", "退租", "承租", "出租")):
        resources.add("viking://resources/laws/civil_code/contract/")
        skills.add("viking://skills/legal/deposit_dispute_workflow/")

    if any(word in text for word in ("合同审查", "合同条款", "违约", "付款", "解除合同")):
        resources.add("viking://resources/laws/civil_code/contract/")
        skills.add("viking://skills/legal/contract_review_checklist/")

    if any(word in text for word in ("证据", "聊天记录", "转账", "录音", "证明")):
        skills.add("viking://skills/legal/evidence_collection_checklist/")

    if any(word in text for word in ("诉讼时效", "时效", "期限", "超过多久")):
        skills.add("viking://skills/legal/limitation_period_reasoning/")

    if any(word in text for word in ("起诉", "法院", "立案", "执行", "民事诉讼法", "管辖")):
        resources.add("viking://resources/laws/civil_procedure/")
        skills.add("viking://skills/legal/lawsuit_filing_workflow/")

    if any(word in text for word in ("消费者权益保护法", "消费者", "退货", "退款", "网购", "食品安全")):
        resources.add("viking://resources/laws/consumer_protection/")

    if any(word in text for word in ("婚姻", "离婚", "夫妻", "抚养", "彩礼", "财产分割")):
        resources.add("viking://resources/laws/marriage_family/")

    if any(word in text for word in ("侵权", "人身损害", "霸凌", "名誉", "肖像", "隐私")):
        resources.add("viking://resources/laws/civil_code/tort/")

    if any(word in text for word in ("刑法", "刑事", "诈骗", "盗窃", "拘留", "犯罪", "报警")):
        resources.add("viking://resources/laws/criminal/")

    # 民法典问题如果没有更细分类，默认落到合同资源，避免完全空白。
    if "民法典" in text and not resources:
        resources.add("viking://resources/laws/civil_code/contract/")

    return ExpectedContext(resource_uris=resources, skill_uris=skills)


def build_context_augmented_query(
    question: str,
    context: VikingContextResult,
    *,
    max_context_chars: int = 900,
) -> str:
    """构造 B 组 query。

    只使用 URI + L0 abstract + 少量 L1 overview，避免把整段 prompt 噪声塞回检索器。
    """
    lines = [f"原始问题：{question}", "", "OpenViking 上下文目录提示："]
    used_chars = 0
    for hit in context.hits:
        if hit.context_type not in {"resource", "skill", "memory"}:
            continue
        snippet = f"- {hit.uri} | {hit.abstract} | {hit.overview}"
        if used_chars + len(snippet) > max_context_chars:
            remaining = max_context_chars - used_chars
            if remaining <= 0:
                break
            snippet = snippet[:remaining]
        used_chars += len(snippet)
        lines.append(snippet)
        if used_chars >= max_context_chars:
            break
    return "\n".join(lines)


def _chunk_ids(chunks: list[Any]) -> list[str]:
    return [str(getattr(chunk, "chunk_id", "")) for chunk in chunks if getattr(chunk, "chunk_id", "")]


def _context_hit(hits: list[VikingContextHit], expected_uris: set[str], context_type: str) -> bool | None:
    if not expected_uris:
        return None
    hit_uris = {hit.uri for hit in hits if hit.context_type == context_type}
    return bool(hit_uris & expected_uris)


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _delta(after: dict[str, float], before: dict[str, float]) -> dict[str, float]:
    keys = sorted(set(after) | set(before))
    return {key: round(after.get(key, 0.0) - before.get(key, 0.0), 6) for key in keys}


def run_context_ab_eval(
    dataset: list[dict[str, Any]],
    *,
    retriever: Any,
    top_k: int = 5,
    limit: int | None = None,
) -> dict[str, Any]:
    """执行 Context Layer A/B 评测。"""
    selected = dataset[:limit] if limit else dataset
    baseline_metrics: list[RetrievalMetrics] = []
    context_metrics: list[RetrievalMetrics] = []
    details: list[dict[str, Any]] = []

    resource_hits: list[float] = []
    skill_hits: list[float] = []
    context_hit_counts: list[float] = []
    prompt_chars: list[float] = []
    query_chars: list[float] = []

    for i, item in enumerate(selected):
        question = item["question"]
        gt_contexts = item["ground_truth_contexts"]
        acceptable_contexts = item.get("acceptable_contexts") or gt_contexts

        if item.get("corpus_status", "in_corpus") == "out_of_corpus":
            details.append({
                "question": question,
                "corpus_status": "out_of_corpus",
                "baseline_retrieved_ids": [],
                "context_retrieved_ids": [],
                "baseline_hit": None,
                "context_hit": None,
            })
            continue

        baseline_ids = _chunk_ids(retriever.retrieve(question, top_k=top_k))
        baseline = compute_retrieval_metrics(baseline_ids, gt_contexts, acceptable_contexts)
        baseline_metrics.append(baseline)

        viking_context = retrieve_viking_context(
            question,
            thread_id=f"eval-ab-{i}",
        )
        context_query = build_context_augmented_query(question, viking_context)
        context_ids = _chunk_ids(retriever.retrieve(context_query, top_k=top_k))
        treatment = compute_retrieval_metrics(context_ids, gt_contexts, acceptable_contexts)
        context_metrics.append(treatment)

        expected = infer_expected_context(item)
        resource_hit = _context_hit(viking_context.hits, expected.resource_uris, "resource")
        skill_hit = _context_hit(viking_context.hits, expected.skill_uris, "skill")
        if resource_hit is not None:
            resource_hits.append(1.0 if resource_hit else 0.0)
        if skill_hit is not None:
            skill_hits.append(1.0 if skill_hit else 0.0)
        context_hit_counts.append(float(len(viking_context.hits)))
        prompt_chars.append(float(len(viking_context.prompt)))
        query_chars.append(float(len(context_query)))

        details.append({
            "question": question,
            "corpus_status": "in_corpus",
            "baseline_query": question,
            "context_query": context_query,
            "expected_resource_uris": sorted(expected.resource_uris),
            "expected_skill_uris": sorted(expected.skill_uris),
            "viking_context_hits": [hit.to_dict() for hit in viking_context.hits],
            "baseline_retrieved_ids": baseline_ids,
            "context_retrieved_ids": context_ids,
            "baseline_hit": baseline.hit,
            "context_hit": treatment.hit,
            "baseline_reciprocal_rank": baseline.reciprocal_rank,
            "context_reciprocal_rank": treatment.reciprocal_rank,
            "baseline_precision": baseline.precision,
            "context_precision": treatment.precision,
            "baseline_recall": baseline.recall,
            "context_recall": treatment.recall,
            "resource_hit": resource_hit,
            "skill_hit": skill_hit,
        })

    baseline_aggregated = aggregate_metrics(baseline_metrics)
    context_aggregated = aggregate_metrics(context_metrics)
    return {
        "mode": "context_ab",
        "top_k": top_k,
        "num_queries": len(baseline_metrics),
        "num_total_queries": len(selected),
        "baseline": {
            "name": "hybrid_retriever_raw_query",
            "aggregated": baseline_aggregated,
        },
        "context_layer": {
            "name": "hybrid_retriever_openviking_context_query",
            "aggregated": context_aggregated,
        },
        "delta": _delta(context_aggregated, baseline_aggregated),
        "context_routing": {
            "resource_hit_rate": _avg(resource_hits),
            "skill_hit_rate": _avg(skill_hits),
            "avg_context_hits": _avg(context_hit_counts),
            "avg_viking_prompt_chars": _avg(prompt_chars),
            "avg_context_query_chars": _avg(query_chars),
            "resource_eval_count": len(resource_hits),
            "skill_eval_count": len(skill_hits),
        },
        "details": details,
    }
