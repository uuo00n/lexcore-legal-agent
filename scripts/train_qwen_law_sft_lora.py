"""Qwen2.5-7B-Instruct 法律问答 LoRA SFT 训练脚本。

说明：
1. 本脚本适用于把 DISC-Law 数据集用于“法律问答生成”SFT。
2. 本脚本不适合直接训练 HyDE / query rewrite 模型，因为 DISC-Law 不是检索改写数据。
3. 默认使用本地模型和数据路径，输出 LoRA 适配器权重。
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from datasets import Dataset
from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    DataCollatorForSeq2Seq,
    Trainer,
    TrainingArguments,
)


DEFAULT_MODEL_PATH = "/Users/didi/Desktop/Legal/models/Qwen2.5-7B-Instruct"
DEFAULT_PAIR_PATH = "/Users/didi/Desktop/Legal/data/DISC-Law-SFT-Pair-QA-released.jsonl"
DEFAULT_TRIPLET_PATH = "/Users/didi/Desktop/Legal/data/DISC-Law-SFT-Triplet-QA-released.jsonl"
DEFAULT_OUTPUT_DIR = "/Users/didi/Desktop/Legal/models/qwen2_5_law_sft_lora"

SYSTEM_PROMPT = (
    "你是一名严谨的中国法律助手。"
    "请基于给定问题或法条参考，输出清晰、准确、简洁的法律分析答案。"
    "不要编造法条，不要输出与问题无关的内容。"
)


@dataclass
class TrainExample:
    """单条训练样本。"""

    prompt: str
    answer: str


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="Qwen2.5 法律问答 LoRA SFT")
    parser.add_argument("--model-path", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--pair-path", default=DEFAULT_PAIR_PATH)
    parser.add_argument("--triplet-path", default=DEFAULT_TRIPLET_PATH)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-length", type=int, default=2048)
    parser.add_argument("--num-train-epochs", type=float, default=2.0)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--per-device-train-batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8)
    parser.add_argument("--logging-steps", type=int, default=20)
    parser.add_argument("--save-strategy", default="epoch", choices=["no", "steps", "epoch"])
    parser.add_argument("--save-steps", type=int, default=200)
    parser.add_argument("--save-total-limit", type=int, default=2)
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--use-4bit", action="store_true")
    parser.add_argument("--allow-cpu", action="store_true")
    return parser.parse_args()


def ensure_runtime(args: argparse.Namespace) -> None:
    """检查训练运行环境。"""
    if torch.cuda.is_available():
        return
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return
    if args.allow_cpu:
        return
    raise RuntimeError(
        "当前环境未检测到 CUDA 或 MPS。Qwen2.5-7B LoRA 训练在纯 CPU 上通常不可行。"
        "请换到有 NVIDIA GPU 的机器上运行，或显式传入 --allow-cpu。"
    )


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """读取 jsonl 文件。"""
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


def build_pair_examples(path: str | Path) -> list[TrainExample]:
    """从 Pair 数据构造法律问答 SFT 样本。"""
    examples: list[TrainExample] = []
    for item in read_jsonl(path):
        question = str(item.get("input", "")).strip()
        answer = str(item.get("output", "")).strip()
        if not question or not answer:
            continue
        prompt = f"请回答下面的中国法律问题：\n\n{question}"
        examples.append(TrainExample(prompt=prompt, answer=answer))
    return examples


def build_triplet_examples(path: str | Path) -> list[TrainExample]:
    """从 Triplet 数据构造带法条参考的法律问答 SFT 样本。"""
    examples: list[TrainExample] = []
    for item in read_jsonl(path):
        references = item.get("reference", [])
        question_block = str(item.get("input", "")).strip()
        answer = str(item.get("output", "")).strip()
        if not question_block or not answer:
            continue
        ref_text = "\n".join(ref.strip() for ref in references if str(ref).strip())
        if ref_text:
            prompt = (
                "请基于给定法条参考回答问题。\n\n"
                f"法条参考：\n{ref_text}\n\n"
                f"问题：\n{question_block}"
            )
        else:
            prompt = f"请回答下面的中国法律问题：\n\n{question_block}"
        examples.append(TrainExample(prompt=prompt, answer=answer))
    return examples


def build_dataset(pair_path: str | Path, triplet_path: str | Path) -> Dataset:
    """构造训练数据集。"""
    examples = build_pair_examples(pair_path) + build_triplet_examples(triplet_path)
    rows = [{"prompt": item.prompt, "answer": item.answer} for item in examples]
    return Dataset.from_list(rows)


def format_messages(tokenizer: AutoTokenizer, prompt: str, answer: str) -> tuple[str, str]:
    """把样本格式化为 Qwen 对话模板。"""
    user_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    full_messages = user_messages + [{"role": "assistant", "content": answer}]

    prompt_text = tokenizer.apply_chat_template(
        user_messages,
        tokenize=False,
        add_generation_prompt=True,
    )
    full_text = tokenizer.apply_chat_template(
        full_messages,
        tokenize=False,
        add_generation_prompt=False,
    )
    return prompt_text, full_text


def tokenize_example(tokenizer: AutoTokenizer, max_length: int, row: dict[str, str]) -> dict[str, Any]:
    """将单条样本编码为 response-only loss 形式。"""
    prompt_text, full_text = format_messages(tokenizer, row["prompt"], row["answer"])
    prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
    full = tokenizer(full_text, truncation=True, max_length=max_length, add_special_tokens=False)

    input_ids = full["input_ids"]
    attention_mask = full["attention_mask"]
    prompt_len = min(len(prompt_ids), len(input_ids))
    labels = [-100] * prompt_len + input_ids[prompt_len:]

    return {
        "input_ids": input_ids,
        "attention_mask": attention_mask,
        "labels": labels,
    }


def load_tokenizer(model_path: str | Path) -> AutoTokenizer:
    """加载 tokenizer。"""
    tokenizer = AutoTokenizer.from_pretrained(model_path, use_fast=True, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    return tokenizer


def can_use_bnb_4bit() -> bool:
    """检查是否可用 bitsandbytes 4bit。"""
    if not torch.cuda.is_available():
        return False
    try:
        import bitsandbytes  # noqa: F401
    except Exception:
        return False
    return True


def load_model(model_path: str | Path, use_4bit: bool) -> AutoModelForCausalLM:
    """加载基础模型。"""
    kwargs: dict[str, Any] = {
        "trust_remote_code": True,
        "low_cpu_mem_usage": True,
    }

    if use_4bit:
        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
        kwargs["quantization_config"] = quant_config
        kwargs["device_map"] = "auto"
        model = AutoModelForCausalLM.from_pretrained(model_path, **kwargs)
        model = prepare_model_for_kbit_training(model)
        return model

    if torch.cuda.is_available():
        kwargs["torch_dtype"] = torch.float16
        kwargs["device_map"] = "auto"
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        kwargs["torch_dtype"] = torch.float16

    return AutoModelForCausalLM.from_pretrained(model_path, **kwargs)


def build_lora_model(model: AutoModelForCausalLM, args: argparse.Namespace) -> AutoModelForCausalLM:
    """给基础模型挂载 LoRA。"""
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
        bias="none",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()
    return model


def build_training_args(args: argparse.Namespace) -> TrainingArguments:
    """构造训练参数。"""
    use_bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    use_fp16 = torch.cuda.is_available() and not use_bf16

    return TrainingArguments(
        output_dir=args.output_dir,
        per_device_train_batch_size=args.per_device_train_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=args.learning_rate,
        num_train_epochs=args.num_train_epochs,
        logging_steps=args.logging_steps,
        save_strategy=args.save_strategy,
        save_steps=args.save_steps,
        save_total_limit=args.save_total_limit,
        lr_scheduler_type="cosine",
        warmup_ratio=args.warmup_ratio,
        fp16=use_fp16,
        bf16=use_bf16,
        gradient_checkpointing=True,
        report_to="none",
        remove_unused_columns=False,
    )


def main() -> None:
    """脚本入口。"""
    args = parse_args()
    ensure_runtime(args)

    model_path = Path(args.model_path)
    pair_path = Path(args.pair_path)
    triplet_path = Path(args.triplet_path)
    output_dir = Path(args.output_dir)

    if not model_path.exists():
        raise FileNotFoundError(f"模型目录不存在: {model_path}")
    if not pair_path.exists():
        raise FileNotFoundError(f"Pair 数据不存在: {pair_path}")
    if not triplet_path.exists():
        raise FileNotFoundError(f"Triplet 数据不存在: {triplet_path}")

    tokenizer = load_tokenizer(model_path)
    dataset = build_dataset(pair_path, triplet_path)
    tokenized = dataset.map(
        lambda row: tokenize_example(tokenizer, args.max_length, row),
        remove_columns=dataset.column_names,
        desc="Tokenizing",
    )

    use_4bit = args.use_4bit and can_use_bnb_4bit()
    if args.use_4bit and not use_4bit:
        print("提示：已请求 --use-4bit，但当前环境不可用，将回退到普通加载。")

    model = load_model(model_path, use_4bit=use_4bit)
    model = build_lora_model(model, args)

    if hasattr(model, "config"):
        model.config.use_cache = False

    output_dir.mkdir(parents=True, exist_ok=True)
    trainer = Trainer(
        model=model,
        args=build_training_args(args),
        train_dataset=tokenized,
        tokenizer=tokenizer,
        data_collator=DataCollatorForSeq2Seq(
            tokenizer=tokenizer,
            padding=True,
            return_tensors="pt",
        ),
    )

    trainer.train()
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    print(f"训练完毕，LoRA 权重保存在: {output_dir}")


if __name__ == "__main__":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    main()
