"""加载 Qwen 基座模型 + LoRA 适配器做本地推理验证。"""
from __future__ import annotations

import argparse
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


DEFAULT_MODEL_PATH = "/Users/didi/Desktop/Legal/models/Qwen2.5-7B-Instruct"
DEFAULT_ADAPTER_PATH = "/Users/didi/Desktop/Legal/models/qwen2_5_law_sft_lora"

SYSTEM_PROMPT = (
    "你是一名严谨的中国法律助手。"
    "请基于问题给出清晰、准确、简洁的法律分析，不要编造法条。"
)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="Qwen2.5 LoRA 本地推理")
    parser.add_argument("--model-path", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--adapter-path", default=DEFAULT_ADAPTER_PATH)
    parser.add_argument("--question", required=True)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.2)
    return parser.parse_args()


def choose_device() -> str:
    """选择推理设备。"""
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def main() -> None:
    """脚本入口。"""
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

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": args.question},
    ]
    prompt = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )
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
    answer = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
    print(answer)


if __name__ == "__main__":
    main()
