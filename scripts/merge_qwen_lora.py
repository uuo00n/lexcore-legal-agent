"""合并 Qwen 基座模型和 LoRA 适配器，输出完整 HuggingFace 模型目录。"""
from __future__ import annotations

import argparse
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


DEFAULT_MODEL_PATH = "/Users/didi/Desktop/Legal/models/Qwen2.5-7B-Instruct"
DEFAULT_ADAPTER_PATH = "/Users/didi/Desktop/Legal/models/qwen2_5_hyde_lora"
DEFAULT_OUTPUT_PATH = "/Users/didi/Desktop/Legal/models/qwen2_5_hyde_merged"


def parse_args() -> argparse.Namespace:
    """
    函数作用：
        解析命令行参数。
    输入参数：
        - 无
    输出参数：
        - argparse.Namespace
    """
    parser = argparse.ArgumentParser(description="合并 Qwen LoRA")
    parser.add_argument("--model-path", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--adapter-path", default=DEFAULT_ADAPTER_PATH)
    parser.add_argument("--output-path", default=DEFAULT_OUTPUT_PATH)
    return parser.parse_args()


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
    model_path = Path(args.model_path)
    adapter_path = Path(args.adapter_path)
    output_path = Path(args.output_path)

    if not model_path.exists():
        raise FileNotFoundError(f"模型目录不存在: {model_path}")
    if not adapter_path.exists():
        raise FileNotFoundError(f"LoRA 目录不存在: {adapter_path}")

    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    base_model = AutoModelForCausalLM.from_pretrained(
        model_path,
        trust_remote_code=True,
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
    )
    model = PeftModel.from_pretrained(base_model, adapter_path)
    merged = model.merge_and_unload()

    output_path.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(output_path, safe_serialization=True)
    tokenizer.save_pretrained(output_path)
    print(f"合并模型已保存: {output_path}")


if __name__ == "__main__":
    main()
