"""RAGAS 评测主脚本 —— 支持检索评测和端到端评测两种模式。

用法：
    python eval/run_eval.py --mode retrieval   # 仅检索评测
    python eval/run_eval.py --mode e2e         # 端到端评测
    python eval/run_eval.py --mode all         # 全部
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("eval")

EVAL_DIR = Path(__file__).parent
DATASET_PATH = EVAL_DIR / "dataset.json"
RESULTS_DIR = EVAL_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def load_dataset() -> list[dict]:
    """
    函数作用：
        加载评测数据集。
    输入参数：
        - 无
    输出参数：
        - list[dict]
    """
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    log.info("加载数据集: %d 条", len(data))
    return data


def run_retrieval_eval(dataset: list[dict], top_k: int = 5) -> dict:
    """
    函数作用：
        检索评测 —— 直接调用 HybridRetriever，对比 chunk_id。
    输入参数：
        - dataset: list[dict]
        - top_k: int，默认值 5
    输出参数：
        - dict
    """
    from services.indexer.chunker import chunk_all_laws
    from services.rag.retriever import init_retriever, get_retriever, reset_retriever
    from eval.metrics import compute_retrieval_metrics, aggregate_metrics, RetrievalMetrics

    log.info("初始化检索器...")
    reset_retriever()
    chunks = chunk_all_laws("data/laws")
    init_retriever(chunks)
    retriever = get_retriever()

    in_corpus_dataset = [
        item for item in dataset
        if item.get("corpus_status", "in_corpus") != "out_of_corpus"
    ]
    out_of_corpus_count = len(dataset) - len(in_corpus_dataset)

    log.info(
        "开始检索评测（%d 条，top_k=%d，out_of_corpus=%d 条不计入汇总）...",
        len(in_corpus_dataset),
        top_k,
        out_of_corpus_count,
    )
    all_metrics: list[RetrievalMetrics] = []
    details: list[dict] = []

    for i, item in enumerate(dataset):
        question = item["question"]
        gt_contexts = item["ground_truth_contexts"]
        acceptable_contexts = item.get("acceptable_contexts") or gt_contexts

        if item.get("corpus_status", "in_corpus") == "out_of_corpus":
            details.append({
                "question": question,
                "ground_truth_contexts": gt_contexts,
                "acceptable_contexts": acceptable_contexts,
                "retrieved_ids": [],
                "hit": None,
                "reciprocal_rank": None,
                "precision": None,
                "recall": None,
                "corpus_status": "out_of_corpus",
            })
            log.info("[%d/%d] SKIP(out_of_corpus) | %s", i + 1, len(dataset), question[:30])
            continue

        results = retriever.retrieve(question, top_k=top_k)
        retrieved_ids = [chunk.chunk_id for chunk in results]

        metrics = compute_retrieval_metrics(retrieved_ids, gt_contexts, acceptable_contexts)
        all_metrics.append(metrics)

        details.append({
            "question": question,
            "ground_truth_contexts": gt_contexts,
            "acceptable_contexts": acceptable_contexts,
            "retrieved_ids": retrieved_ids,
            "hit": metrics.hit,
            "reciprocal_rank": metrics.reciprocal_rank,
            "precision": metrics.precision,
            "recall": metrics.recall,
            "corpus_status": "in_corpus",
        })

        status = "HIT" if metrics.hit else "MISS"
        log.info("[%d/%d] %s | %s", i + 1, len(dataset), status, question[:30])

    aggregated = aggregate_metrics(all_metrics)

    log.info("=" * 60)
    log.info("检索评测结果:")
    log.info("  Hit Rate:  %.2f%%", aggregated["hit_rate"] * 100)
    log.info("  MRR:       %.4f", aggregated["mrr"])
    log.info("  Precision: %.4f", aggregated["precision"])
    log.info("  Recall:    %.4f", aggregated["recall"])
    log.info("=" * 60)

    return {
        "mode": "retrieval",
        "top_k": top_k,
        "num_queries": len(in_corpus_dataset),
        "num_total_queries": len(dataset),
        "num_out_of_corpus": out_of_corpus_count,
        "aggregated": aggregated,
        "details": details,
    }


def run_context_ab_eval_with_retriever(
    dataset: list[dict],
    top_k: int = 5,
    limit: int | None = None,
    fast: bool = False,
) -> dict:
    """
    函数作用：
        OpenViking Context Layer A/B 评测。
    输入参数：
        - dataset: list[dict]
        - top_k: int，默认值 5
        - limit: int | None，默认值 None
        - fast: bool，默认值 False
    输出参数：
        - dict
    """
    from services.indexer.chunker import chunk_all_laws
    from services.rag.retriever import init_retriever, get_retriever, reset_retriever
    from contextlib import nullcontext

    from eval.context_ab import disabled_query_enhancement, run_context_ab_eval

    log.info("初始化检索器...")
    reset_retriever()
    chunks = chunk_all_laws("data/laws")
    init_retriever(chunks)
    retriever = get_retriever()

    log.info(
        "开始 OpenViking Context Layer A/B 评测（top_k=%d, limit=%s）...",
        top_k,
        limit or "all",
    )
    query_context = disabled_query_enhancement() if fast else nullcontext()
    with query_context:
        results = run_context_ab_eval(dataset, retriever=retriever, top_k=top_k, limit=limit)
    results["fast"] = fast
    baseline = results["baseline"]["aggregated"]
    context_layer = results["context_layer"]["aggregated"]
    delta = results["delta"]
    routing = results["context_routing"]

    log.info("=" * 60)
    log.info("Context A/B 评测结果:")
    log.info("  Baseline Hit Rate: %.2f%%", baseline["hit_rate"] * 100)
    log.info("  Context  Hit Rate: %.2f%%", context_layer["hit_rate"] * 100)
    log.info("  Δ Hit Rate:        %.2f%%", delta["hit_rate"] * 100)
    log.info("  Baseline MRR:      %.4f", baseline["mrr"])
    log.info("  Context  MRR:      %.4f", context_layer["mrr"])
    log.info("  Δ MRR:             %.4f", delta["mrr"])
    log.info("  Resource Hit Rate: %.2f%%", routing["resource_hit_rate"] * 100)
    log.info("  Skill Hit Rate:    %.2f%%", routing["skill_hit_rate"] * 100)
    log.info("=" * 60)
    return results


def run_openviking_ab_eval_with_retriever(
    dataset: list[dict],
    top_k: int = 5,
    limit: int | None = None,
    openviking_limit: int = 20,
    fast: bool = False,
) -> dict:
    """
    函数作用：
        真实 OpenViking A/B 评测。
    输入参数：
        - dataset: list[dict]
        - top_k: int，默认值 5
        - limit: int | None，默认值 None
        - openviking_limit: int，默认值 20
        - fast: bool，默认值 False
    输出参数：
        - dict
    """
    from contextlib import nullcontext

    from eval.context_ab import disabled_query_enhancement
    from services.indexer.chunker import chunk_all_laws
    from services.openviking_client import OpenVikingHTTPClient, OpenVikingSettings
    from services.rag.retriever import init_retriever, get_retriever, reset_retriever

    from eval.openviking_ab import run_openviking_ab_eval

    log.info("初始化检索器...")
    reset_retriever()
    chunks = chunk_all_laws("data/laws")
    init_retriever(chunks)
    retriever = get_retriever()

    settings = OpenVikingSettings.from_env()
    client = OpenVikingHTTPClient(settings)
    try:
        log.info(
            "开始真实 OpenViking A/B 评测（top_k=%d, limit=%s, openviking_limit=%d, url=%s）...",
            top_k,
            limit or "all",
            openviking_limit,
            settings.base_url,
        )
        query_context = disabled_query_enhancement() if fast else nullcontext()
        with query_context:
            results = run_openviking_ab_eval(
                dataset,
                retriever=retriever,
                openviking_client=client,
                top_k=top_k,
                limit=limit,
                openviking_limit=openviking_limit,
            )
    finally:
        client.close()

    results["fast"] = fast
    baseline = results["baseline"]["aggregated"]
    openviking = results["openviking"]["aggregated"]
    delta = results["delta"]
    routing = results["openviking_routing"]

    log.info("=" * 60)
    log.info("真实 OpenViking A/B 评测结果:")
    log.info("  Baseline Hit Rate:   %.2f%%", baseline["hit_rate"] * 100)
    log.info("  OpenViking Hit Rate: %.2f%%", openviking["hit_rate"] * 100)
    log.info("  Δ Hit Rate:          %.2f%%", delta["hit_rate"] * 100)
    log.info("  Baseline MRR:        %.4f", baseline["mrr"])
    log.info("  OpenViking MRR:      %.4f", openviking["mrr"])
    log.info("  Δ MRR:               %.4f", delta["mrr"])
    log.info("  Resource Hit Rate:   %.2f%%", routing["resource_hit_rate"] * 100)
    log.info("=" * 60)
    return results


async def run_e2e_eval(dataset: list[dict]) -> dict:
    """
    函数作用：
        端到端评测 —— 调用完整 Agent，用 RAGAS LLM-as-judge 评估。
    输入参数：
        - dataset: list[dict]
    输出参数：
        - dict
    """
    from ragas import evaluate
    from ragas.metrics import Faithfulness, AnswerRelevancy, AnswerCorrectness
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.dataset_schema import SingleTurnSample, EvaluationDataset
    from langchain_core.messages import HumanMessage
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    from agent.graph import build_graph
    from services.rag.startup import initialize_rag

    log.info("初始化进程内 RAG...")
    initialize_rag()

    graph = build_graph(checkpointer=None)

    log.info("开始端到端评测（%d 条）...", len(dataset))
    samples: list[SingleTurnSample] = []

    for i, item in enumerate(dataset):
        question = item["question"]
        ground_truth = item["ground_truth"]

        try:
            result = await graph.ainvoke(
                {
                    "messages": [HumanMessage(content=question)],
                    "thread_id": f"eval-{i}",
                },
                {"configurable": {"thread_id": f"eval-{i}"}},
            )

            answer = result["messages"][-1].content or ""
            retrieved_laws = result.get("retrieved_laws", [])
            contexts = [
                law.get("content", f"《{law.get('law_name', '')}》{law.get('article_no', '')}")
                for law in retrieved_laws
            ]

            if not contexts:
                contexts = ["未检索到相关法条"]

        except Exception as e:
            log.warning("[%d] Agent 调用失败: %s", i + 1, e)
            answer = f"评测错误: {e}"
            contexts = ["评测错误"]

        samples.append(SingleTurnSample(
            user_input=question,
            response=answer,
            retrieved_contexts=contexts,
            reference=ground_truth,
        ))

        log.info("[%d/%d] 完成 | %s", i + 1, len(dataset), question[:30])

    log.info("使用 RAGAS 评估回答质量...")

    evaluator_llm = LangchainLLMWrapper(ChatOpenAI(
        base_url=os.getenv("LLM_BASE_URL_OVERRIDE"),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        model=os.getenv("LLM_MODEL", "deepseek-ai/DeepSeek-V4-Flash"),
        temperature=0,
    ))

    evaluator_embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings(
        base_url=os.getenv("LLM_BASE_URL_OVERRIDE"),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        model="BAAI/bge-small-zh-v1.5",
    ))

    metrics = [
        Faithfulness(llm=evaluator_llm),
        AnswerRelevancy(llm=evaluator_llm, embeddings=evaluator_embeddings),
        AnswerCorrectness(llm=evaluator_llm),
    ]

    eval_dataset = EvaluationDataset(samples=samples)
    ragas_result = evaluate(dataset=eval_dataset, metrics=metrics)

    scores = ragas_result.to_pandas()
    avg_scores = {
        "faithfulness": float(scores["faithfulness"].mean()),
        "answer_relevancy": float(scores["answer_relevancy"].mean()),
        "answer_correctness": float(scores["answer_correctness"].mean()),
    }

    log.info("=" * 60)
    log.info("端到端评测结果:")
    log.info("  Faithfulness:      %.4f", avg_scores["faithfulness"])
    log.info("  Answer Relevancy:  %.4f", avg_scores["answer_relevancy"])
    log.info("  Answer Correctness:%.4f", avg_scores["answer_correctness"])
    log.info("=" * 60)

    details = []
    for i, row in scores.iterrows():
        details.append({
            "question": dataset[i]["question"],
            "answer": samples[i].response[:200],
            "faithfulness": float(row["faithfulness"]) if not None else None,
            "answer_relevancy": float(row["answer_relevancy"]) if not None else None,
            "answer_correctness": float(row["answer_correctness"]) if not None else None,
        })

    return {
        "mode": "e2e",
        "num_queries": len(dataset),
        "aggregated": avg_scores,
        "details": details,
    }


def save_results(results: dict) -> Path:
    """
    函数作用：
        保存评测结果到 JSON 文件。
    输入参数：
        - results: dict
    输出参数：
        - Path
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = results.get("mode", "unknown")
    filename = f"eval_{mode}_{timestamp}.json"
    path = RESULTS_DIR / filename

    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    log.info("结果已保存: %s", path)
    try:
        from services.checkpoint import get_meta_conn, init_meta_db
        from services.observability import init_observability_tables, record_eval_run

        try:
            get_meta_conn()
        except RuntimeError:
            init_meta_db()
        init_observability_tables()
        record_eval_run(results, str(path))
        log.info("评测历史已写入 SQLite")
    except Exception as exc:
        log.warning("评测历史写入失败（JSON 结果已保存）: %s", exc)
    return path


async def main():
    """
    函数作用：
        待补充。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    parser = argparse.ArgumentParser(description="法智 RAG 评测工具")
    parser.add_argument(
        "--mode",
        choices=["retrieval", "context_ab", "openviking_ab", "e2e", "all"],
        default="retrieval",
        help=(
            "评测模式: retrieval=仅检索, context_ab=本地Context Layer A/B, "
            "openviking_ab=真实OpenViking A/B, e2e=端到端, all=全部"
        ),
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="检索评测的 top_k 参数（默认 5）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制评测条数，适合 smoke test（默认全量）",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="context_ab/openviking_ab 使用：临时关闭 HyDE/rewrite，加快 smoke test 或全量对照；默认关闭",
    )
    parser.add_argument(
        "--openviking-limit",
        type=int,
        default=20,
        help="仅 openviking_ab 使用：每题从 OpenViking find 返回的资源数量（默认 20）",
    )
    args = parser.parse_args()

    from services.checkpoint import init_meta_db
    from services.observability import init_observability_tables
    init_meta_db()
    init_observability_tables()

    dataset = load_dataset()

    if args.mode in ("retrieval", "all"):
        retrieval_results = run_retrieval_eval(dataset, top_k=args.top_k)
        save_results(retrieval_results)

    if args.mode in ("context_ab", "all"):
        context_ab_results = run_context_ab_eval_with_retriever(
            dataset,
            top_k=args.top_k,
            limit=args.limit,
            fast=args.fast,
        )
        save_results(context_ab_results)

    if args.mode in ("openviking_ab", "all"):
        openviking_ab_results = run_openviking_ab_eval_with_retriever(
            dataset,
            top_k=args.top_k,
            limit=args.limit,
            openviking_limit=args.openviking_limit,
            fast=args.fast,
        )
        save_results(openviking_ab_results)

    if args.mode in ("e2e", "all"):
        e2e_results = await run_e2e_eval(dataset)
        save_results(e2e_results)


if __name__ == "__main__":
    asyncio.run(main())
