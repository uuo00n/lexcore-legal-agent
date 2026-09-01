"""查询增强模块 —— 问题重写 + HyDE 假设文档生成。

两阶段查询增强策略：
1. 问题重写：将用户口语化表达改写为精确的法律检索 query
2. HyDE：生成假设性法条文本，用于语义检索的 embedding

两者先各自生成增强文本，再进入多路召回：
- 重写后的 query → BM25 关键词检索 + 语义向量检索
- HyDE 假设文档 → 语义向量检索 + BM25 关键词检索
- 原始 query → BM25 关键词检索 + 语义向量检索
- 原始 query → Reranker 精排（真实相关性判断）
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI

log = logging.getLogger("legal.query_enhance")

DEFAULT_HYDE_MODEL = "deepseek-v4-flash"
DEFAULT_HYDE_LLM_BASE_URL = "https://api.deepseek.com"
# 旧本地小模型配置先保留，后续如需切回可改回这两个值：
# LEGACY_HYDE_MODEL = "qwen2.5:1.5b"
# LEGACY_HYDE_LLM_BASE_URL = "http://localhost:11434/v1"

_REWRITE_PROMPT = (
    "你是一个法律检索查询优化器。将用户的口语化法律问题改写为适合检索的精确查询。\n"
    "要求：\n"
    "- 提取核心法律概念和关键词\n"
    "- 补充隐含的法律术语\n"
    "- 去除口语化表达、语气词\n"
    "- 输出 1-2 句精炼的检索 query\n"
    "- 直接输出改写结果，不要解释\n\n"
    "用户问题：{query}"
)

_HYDE_PROMPT = (
    "你是一个中国法律条文生成器。根据用户的法律问题，"
    "生成一段 50-100 字的假设性法律条文原文，"
    "模拟真实法条的语言风格和结构。"
    "不需要真实存在，只需要语义上与问题高度相关。"
    "直接输出法条文本，不要加任何解释。\n\n"
    "用户问题：{query}"
)


def _get_enhance_llm() -> "ChatOpenAI":
    """
    函数作用：
        获取查询增强专用的轻量 LLM 实例。
    输入参数：
        - 无
    输出参数：
        - ChatOpenAI
    """
    model = os.getenv("HYDE_MODEL", DEFAULT_HYDE_MODEL)
    base_url = os.getenv("HYDE_LLM_BASE_URL", DEFAULT_HYDE_LLM_BASE_URL)
    api_key = _get_hyde_api_key(base_url)
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=base_url,
        model=model,
        api_key=api_key,
        temperature=0.7,
        streaming=False,
        max_tokens=150,
    )


def _get_hyde_api_key(base_url: str) -> str:
    """获取 HyDE / query rewrite 使用的 API key。"""
    explicit = os.getenv("HYDE_API_KEY")
    if explicit:
        return explicit
    if "localhost" in base_url or "127.0.0.1" in base_url:
        return "ollama"
    return os.getenv("DEEPSEEK_API_KEY", "missing-deepseek-api-key")


def _get_hyde_backend() -> str:
    """
    函数作用：
        获取 HyDE 生成后端类型。
    输入参数：
        - 无
    输出参数：
        - str
    """
    return os.getenv("HYDE_BACKEND", "openai").strip().lower()


def _build_hf_hyde_prompt(tokenizer, query: str) -> str:
    """
    函数作用：
        构造 HuggingFace LoRA HyDE 生成 prompt。
    输入参数：
        - tokenizer: 未标注
        - query: str
    输出参数：
        - str
    """
    system_prompt = (
        "你是法律 RAG 检索系统中的 HyDE 模块。"
        "根据用户法律问题生成一段用于语义向量检索的假设性法律文档。"
        "不要回答用户，不要给建议，只输出检索用文本。"
    )
    user_prompt = (
        "请根据用户法律问题生成一段用于语义检索的假设性法律文档。"
        "要求包含核心法律概念、可能涉及的法律关系、责任类型和检索关键词；"
        "不要给用户建议，不要写结论，只输出检索用文本。\n\n"
        f"用户问题：{query}"
    )
    return tokenizer.apply_chat_template(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tokenize=False,
        add_generation_prompt=True,
    )


@lru_cache(maxsize=1)
def _get_hf_hyde_components():
    """
    函数作用：
        懒加载 HuggingFace 基座模型和 HyDE LoRA 适配器。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model_path = os.getenv(
        "HYDE_HF_MODEL_PATH",
        "/Users/didi/Desktop/Legal/models/Qwen2.5-7B-Instruct",
    )
    adapter_path = os.getenv(
        "HYDE_LORA_PATH",
        "/Users/didi/Desktop/Legal/models/qwen2_5_hyde_lora",
    )

    if torch.cuda.is_available():
        device = "cuda"
        dtype = torch.float16
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        device = "mps"
        dtype = torch.float16
    else:
        device = "cpu"
        dtype = torch.float32

    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    base_model = AutoModelForCausalLM.from_pretrained(
        model_path,
        trust_remote_code=True,
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
    )
    model = PeftModel.from_pretrained(base_model, adapter_path)
    model.to(device)
    model.eval()
    return tokenizer, model, device


def _generate_hyde_with_hf_lora(query: str) -> str:
    """
    函数作用：
        使用本地 HuggingFace + LoRA 生成 HyDE 假设文档。
    输入参数：
        - query: str
    输出参数：
        - str
    """
    import torch

    tokenizer, model, device = _get_hf_hyde_components()
    prompt = _build_hf_hyde_prompt(tokenizer, query)
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    max_new_tokens = int(os.getenv("HYDE_HF_MAX_NEW_TOKENS", "220"))
    temperature = float(os.getenv("HYDE_HF_TEMPERATURE", "0.2"))

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=temperature > 0,
            temperature=temperature,
            top_p=0.9,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
    new_tokens = outputs[0][inputs["input_ids"].shape[1]:]
    return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


def is_query_enhance_enabled() -> bool:
    """
    函数作用：
        检查查询增强是否启用。
    输入参数：
        - 无
    输出参数：
        - bool
    """
    return os.getenv("HYDE_ENABLED", "true").lower() in ("true", "1", "yes")


def rewrite_query(query: str) -> str:
    """
    函数作用：
        将用户口语化问题改写为精确的法律检索 query。
    输入参数：
        - query: str
    输出参数：
        - str
    """
    if os.getenv("HYDE_REWRITE_ENABLED", "true").lower() not in ("true", "1", "yes"):
        return query

    try:
        llm = _get_enhance_llm()
        prompt = _REWRITE_PROMPT.format(query=query)
        response = llm.invoke(prompt)
        rewritten = response.content.strip()
        # 只记长度，不把可能含个人隐私的原始问题或改写文本写入普通日志。
        log.debug("问题重写完成: input_chars=%s output_chars=%s", len(query), len(rewritten))
        return rewritten
    except Exception as exc:
        log.warning("问题重写失败，回退到原始 query: error_type=%s", type(exc).__name__)
        return query


def generate_hypothetical_doc(query: str) -> str:
    """
    函数作用：
        根据用户问题生成假设性法条文本，用于语义检索。
    输入参数：
        - query: str
    输出参数：
        - str
    """
    if _get_hyde_backend() == "hf_lora":
        try:
            hypothetical = _generate_hyde_with_hf_lora(query)
            log.debug(
                "HyDE LoRA 假设文档生成完成: input_chars=%s output_chars=%s",
                len(query),
                len(hypothetical),
            )
            return hypothetical
        except Exception as exc:
            log.warning("HyDE LoRA 生成失败，回退到原始 query: error_type=%s", type(exc).__name__)
            return query

    try:
        llm = _get_enhance_llm()
        prompt = _HYDE_PROMPT.format(query=query)
        response = llm.invoke(prompt)
        hypothetical = response.content.strip()
        log.debug(
            "HyDE 假设文档生成完成: input_chars=%s output_chars=%s",
            len(query),
            len(hypothetical),
        )
        return hypothetical
    except Exception as exc:
        log.warning("HyDE 生成失败，回退到原始 query: error_type=%s", type(exc).__name__)
        return query
