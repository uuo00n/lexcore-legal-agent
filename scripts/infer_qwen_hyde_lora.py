"""加载 Qwen HyDE LoRA 生成假设性法律检索文档。"""
from __future__ import annotations

import argparse
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


DEFAULT_MODEL_PATH = "/Users/didi/Desktop/Legal/models/Qwen2.5-7B-Instruct"
DEFAULT_ADAPTER_PATH = "/Users/didi/Desktop/Legal/models/qwen2_5_hyde_lora"

SYSTEM_PROMPT = (
    "你是法律 RAG 检索系统中的 HyDE 模块。"
    "根据用户法律问题生成一段用于语义向量检索的假设性法律文档。"
    "不要回答用户，不要给建议，只输出检索用文本。"
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
    parser = argparse.ArgumentParser(description="Qwen HyDE LoRA 推理")
    parser.add_argument("--model-path", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--adapter-path", default=DEFAULT_ADAPTER_PATH)
    parser.add_argument("--question", required=True)
    parser.add_argument("--max-new-tokens", type=int, default=220)
    parser.add_argument("--temperature", type=float, default=0.2)
    return parser.parse_args()


def choose_device() -> str:
    """
    函数作用：
        选择可用推理设备。
    输入参数：
        - 无
    输出参数：
        - str
    """
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def build_prompt(tokenizer: AutoTokenizer, question: str) -> str:
    """
    函数作用：
        构造 Qwen chat prompt。
    输入参数：
        - tokenizer: AutoTokenizer
        - question: str
    输出参数：
        - str
    """
    user_prompt = (
        "请根据用户法律问题生成一段用于语义检索的假设性法律文档。"
        "要求包含核心法律概念、可能涉及的法律关系、责任类型和检索关键词；"
        "不要给用户建议，不要写结论，只输出检索用文本。\n\n"
        f"用户问题：{question}"
    )
    return tokenizer.apply_chat_template(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        tokenize=False,
        add_generation_prompt=True,
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
    model_path = Path(args.model_path)
    adapter_path = Path(args.adapter_path)
    if not model_path.exists():
        raise FileNotFoundError(f"模型目录不存在: {model_path}")
    if not adapter_path.exists():
        raise FileNotFoundError(f"LoRA 目录不存在: {adapter_path}")

    device = choose_device()
    dtype = torch.float16 if device in {"cuda", "mps"} else torch.float32
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    base_model = AutoModelForCausalLM.from_pretrained(
        model_path,
        trust_remote_code=True,
        torch_dtype=dtype,
    )
    model = PeftModel.from_pretrained(base_model, adapter_path)
    model.to(device)
    model.eval()

    prompt = build_prompt(tokenizer, args.question)
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=args.max_new_tokens,
            do_sample=args.temperature > 0,
            temperature=args.temperature,
            top_p=0.9,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
    new_tokens = outputs[0][inputs["input_ids"].shape[1]:]
    print(tokenizer.decode(new_tokens, skip_special_tokens=True).strip())


if __name__ == "__main__":
    main()
