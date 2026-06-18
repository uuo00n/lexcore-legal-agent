"""构造 HyDE 假设文档生成的 SFT 数据集。

DISC-Law 的输出是法律问答答案，不能直接当 query rewrite 标签。
但它很适合训练 HyDE：输入用户法律问题，输出一段带法律概念和法条语义的
“假设性法律文档”，再拿这段文本做向量检索。
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path
from typing import Any


DEFAULT_PAIR_PATH = "/Users/didi/Desktop/Legal/data/DISC-Law-SFT-Pair-QA-released.jsonl"
DEFAULT_TRIPLET_PATH = "/Users/didi/Desktop/Legal/data/DISC-Law-SFT-Triplet-QA-released.jsonl"
DEFAULT_OUTPUT_PATH = "/Users/didi/Desktop/Legal/data/finetune/hyde_sft_train.jsonl"


def parse_args() -> argparse.Namespace:
    """
    函数作用：
        解析命令行参数。
    输入参数：
        - 无
    输出参数：
        - argparse.Namespace
    """
    parser = argparse.ArgumentParser(description="构造 HyDE SFT 训练数据")
    parser.add_argument("--pair-path", default=DEFAULT_PAIR_PATH)
    parser.add_argument("--triplet-path", default=DEFAULT_TRIPLET_PATH)
    parser.add_argument("--output-path", default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--max-answer-chars", type=int, default=700)
    parser.add_argument("--max-samples", type=int, default=0, help="0 表示不限制")
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """
    函数作用：
        读取 JSONL 文件并跳过坏行。
    输入参数：
        - path: str | Path
    输出参数：
        - list[dict[str, Any]]
    """
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def clean_text(text: str, max_chars: int) -> str:
    """
    函数作用：
        清理训练目标文本并限制长度。
    输入参数：
        - text: str
        - max_chars: int
    输出参数：
        - str
    """
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars].strip()


def extract_triplet_question(text: str) -> str:
    """
    函数作用：
        从 Triplet 的 input 字段里提取真正的用户问题。
    输入参数：
        - text: str
    输出参数：
        - str
    """
    match = re.search(r"<问题>\s*[：:]\s*(.+)", text, flags=re.S)
    if match:
        return clean_text(match.group(1), max_chars=1000)
    return clean_text(text, max_chars=1000)


def make_prompt(question: str) -> str:
    """
    函数作用：
        构造 HyDE 训练输入。
    输入参数：
        - question: str
    输出参数：
        - str
    """
    return (
        "请根据用户法律问题生成一段用于语义检索的假设性法律文档。"
        "要求包含核心法律概念、可能涉及的法律关系、责任类型和检索关键词；"
        "不要给用户建议，不要写结论，只输出检索用文本。\n\n"
        f"用户问题：{question}"
    )


def build_pair_rows(path: str | Path, max_answer_chars: int) -> list[dict[str, str]]:
    """
    函数作用：
        从 Pair 问答数据构造 HyDE 样本。
    输入参数：
        - path: str | Path
        - max_answer_chars: int
    输出参数：
        - list[dict[str, str]]
    """
    rows: list[dict[str, str]] = []
    for item in read_jsonl(path):
        question = clean_text(str(item.get("input", "")), max_chars=1000)
        answer = clean_text(str(item.get("output", "")), max_chars=max_answer_chars)
        if not question or not answer:
            continue
        rows.append(
            {
                "id": str(item.get("id", "")),
                "source": "pair",
                "prompt": make_prompt(question),
                "answer": answer,
            }
        )
    return rows


def build_triplet_rows(path: str | Path, max_answer_chars: int) -> list[dict[str, str]]:
    """
    函数作用：
        从 Triplet 法条参考问答数据构造 HyDE 样本。
    输入参数：
        - path: str | Path
        - max_answer_chars: int
    输出参数：
        - list[dict[str, str]]
    """
    rows: list[dict[str, str]] = []
    for item in read_jsonl(path):
        question = extract_triplet_question(str(item.get("input", "")))
        references = item.get("reference", [])
        ref_text = " ".join(str(ref).strip() for ref in references if str(ref).strip())
        answer = str(item.get("output", "")).strip()
        target = clean_text(f"{ref_text} {answer}", max_chars=max_answer_chars)
        if not question or not target:
            continue
        rows.append(
            {
                "id": str(item.get("id", "")),
                "source": "triplet",
                "prompt": make_prompt(question),
                "answer": target,
            }
        )
    return rows


def main() -> None:
    """
    函数作用：
        脚本入口。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    args = parse_args()
    pair_path = Path(args.pair_path)
    triplet_path = Path(args.triplet_path)
    output_path = Path(args.output_path)

    if not pair_path.exists():
        raise FileNotFoundError(f"Pair 数据不存在: {pair_path}")
    if not triplet_path.exists():
        raise FileNotFoundError(f"Triplet 数据不存在: {triplet_path}")

    rows = build_pair_rows(pair_path, args.max_answer_chars)
    rows.extend(build_triplet_rows(triplet_path, args.max_answer_chars))

    random.seed(args.seed)
    random.shuffle(rows)
    if args.max_samples > 0:
        rows = rows[: args.max_samples]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"HyDE SFT 数据已生成: {output_path}")
    print(f"样本数: {len(rows)}")


if __name__ == "__main__":
    main()
