"""Qwen2.5 HyDE 假设文档生成 LoRA 训练脚本。"""
from __future__ import annotations

import argparse
import json
import os
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
DEFAULT_DATASET_PATH = "/Users/didi/Desktop/Legal/data/finetune/hyde_sft_train.jsonl"
DEFAULT_OUTPUT_DIR = "/Users/didi/Desktop/Legal/models/qwen2_5_hyde_lora"

SYSTEM_PROMPT = (
    "你是法律 RAG 检索系统中的 HyDE 模块。"
    "你的任务是根据用户法律问题生成一段用于语义向量检索的假设性法律文档。"
    "输出应包含法律概念、事实要素、责任类型、可能涉及的法条语义，不要回答用户问题。"
)


def parse_args() -> argparse.Namespace:
    """
    函数作用：
        解析命令行参数。
    输入参数：
        - 无
    输出参数：
        - argparse.Namespace
    """
    parser = argparse.ArgumentParser(description="Qwen2.5 HyDE LoRA SFT")
    parser.add_argument("--model-path", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--dataset-path", default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-length", type=int, default=1536)
    parser.add_argument("--num-train-epochs", type=float, default=1.0)
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
    """
    函数作用：
        检查当前机器是否适合训练。
    输入参数：
        - args: argparse.Namespace
    输出参数：
        - 无
    """
    if torch.cuda.is_available():
        return
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return
    if args.allow_cpu:
        return
    raise RuntimeError(
        "当前环境未检测到 CUDA 或 MPS。Qwen2.5-7B LoRA 训练在纯 CPU 上通常不可行。"
        "请在 GPU 机器运行，或显式传入 --allow-cpu。"
    )


def read_dataset(path: str | Path) -> Dataset:
    """
    函数作用：
        读取 HyDE SFT 数据集。
    输入参数：
        - path: str | Path
    输出参数：
        - Dataset
    """
    rows: list[dict[str, str]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            prompt = str(item.get("prompt", "")).strip()
            answer = str(item.get("answer", "")).strip()
            if prompt and answer:
                rows.append({"prompt": prompt, "answer": answer})
    return Dataset.from_list(rows)


def load_tokenizer(model_path: str | Path) -> AutoTokenizer:
    """
    函数作用：
        加载 Qwen tokenizer。
    输入参数：
        - model_path: str | Path
    输出参数：
        - AutoTokenizer
    """
    tokenizer = AutoTokenizer.from_pretrained(model_path, use_fast=True, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    return tokenizer


def format_messages(tokenizer: AutoTokenizer, prompt: str, answer: str) -> tuple[str, str]:
    """
    函数作用：
        使用 Qwen chat template 格式化训练文本。
    输入参数：
        - tokenizer: AutoTokenizer
        - prompt: str
        - answer: str
    输出参数：
        - tuple[str, str]
    """
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
    """
    函数作用：
        将样本编码为只训练 assistant 输出的 labels。
    输入参数：
        - tokenizer: AutoTokenizer
        - max_length: int
        - row: dict[str, str]
    输出参数：
        - dict[str, Any]
    """
    prompt_text, full_text = format_messages(tokenizer, row["prompt"], row["answer"])
    prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
    full = tokenizer(full_text, truncation=True, max_length=max_length, add_special_tokens=False)
    input_ids = full["input_ids"]
    prompt_len = min(len(prompt_ids), len(input_ids))
    labels = [-100] * prompt_len + input_ids[prompt_len:]
    return {
        "input_ids": input_ids,
        "attention_mask": full["attention_mask"],
        "labels": labels,
    }


def can_use_bnb_4bit() -> bool:
    """
    函数作用：
        检查 bitsandbytes 4bit 量化是否可用。
    输入参数：
        - 无
    输出参数：
        - bool
    """
    if not torch.cuda.is_available():
        return False
    try:
        import bitsandbytes  # noqa: F401
    except Exception:
        return False
    return True


def load_model(model_path: str | Path, use_4bit: bool) -> AutoModelForCausalLM:
    """
    函数作用：
        加载基础模型。
    输入参数：
        - model_path: str | Path
        - use_4bit: bool
    输出参数：
        - AutoModelForCausalLM
    """
    kwargs: dict[str, Any] = {"trust_remote_code": True, "low_cpu_mem_usage": True}
    if use_4bit:
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
        kwargs["device_map"] = "auto"
        model = AutoModelForCausalLM.from_pretrained(model_path, **kwargs)
        return prepare_model_for_kbit_training(model)
    if torch.cuda.is_available():
        kwargs["torch_dtype"] = torch.float16
        kwargs["device_map"] = "auto"
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        kwargs["torch_dtype"] = torch.float16
    return AutoModelForCausalLM.from_pretrained(model_path, **kwargs)


def attach_lora(model: AutoModelForCausalLM, args: argparse.Namespace) -> AutoModelForCausalLM:
    """
    函数作用：
        给基础模型挂载 LoRA 适配器。
    输入参数：
        - model: AutoModelForCausalLM
        - args: argparse.Namespace
    输出参数：
        - AutoModelForCausalLM
    """
    config = LoraConfig(
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
    model = get_peft_model(model, config)
    model.print_trainable_parameters()
    return model


def build_training_args(args: argparse.Namespace) -> TrainingArguments:
    """
    函数作用：
        构造 Trainer 参数。
    输入参数：
        - args: argparse.Namespace
    输出参数：
        - TrainingArguments
    """
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
    """
    函数作用：
        脚本入口。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    args = parse_args()
    ensure_runtime(args)

    model_path = Path(args.model_path)
    dataset_path = Path(args.dataset_path)
    output_dir = Path(args.output_dir)
    if not model_path.exists():
        raise FileNotFoundError(f"模型目录不存在: {model_path}")
    if not dataset_path.exists():
        raise FileNotFoundError(f"训练数据不存在: {dataset_path}")

    tokenizer = load_tokenizer(model_path)
    dataset = read_dataset(dataset_path)
    tokenized = dataset.map(
        lambda row: tokenize_example(tokenizer, args.max_length, row),
        remove_columns=dataset.column_names,
        desc="Tokenizing HyDE dataset",
    )

    use_4bit = args.use_4bit and can_use_bnb_4bit()
    if args.use_4bit and not use_4bit:
        print("提示：--use-4bit 当前不可用，已回退到普通加载。")

    model = load_model(model_path, use_4bit=use_4bit)
    model = attach_lora(model, args)
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
    print(f"HyDE LoRA 权重已保存: {output_dir}")


if __name__ == "__main__":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    main()
