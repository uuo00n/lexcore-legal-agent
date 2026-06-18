"""LLM 客户端工厂 —— 多 Provider 抽象。

DeepSeek / 通义千问 / Ollama 都暴露 OpenAI 兼容协议，
统一通过 langchain_openai.ChatOpenAI + base_url 切换。
"""
from __future__ import annotations

import os
from typing import Any

from langchain_openai import ChatOpenAI

from services.gateway import GatewayChatModel, LLMClientConfig


PROVIDERS: dict[str, dict[str, Any]] = {
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4.7",
        "api_key_env": "ZHIPU_API_KEY",
        "supports_tools": True, },

    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "supports_tools": True,
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "api_key_env": "DASHSCOPE_API_KEY",
        "supports_tools": True,
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "qwen2.5:7b",
        "api_key_env": None,
        "supports_tools": True,
    },
}


def _resolve_provider(provider: str | None) -> str:
    """
    函数作用：
        待补充。
    输入参数：
        - provider: str | None
    输出参数：
        - str
    """
    name = (provider or os.getenv("LLM_PROVIDER") or "zhipu").lower()
    if name not in PROVIDERS:
        raise ValueError(
            f"unknown LLM_PROVIDER: {name!r}, expected one of {list(PROVIDERS)}"
        )
    return name


def _build_chat_client(name: str, overrides: dict[str, Any], *, model_route: str = "") -> LLMClientConfig:
    """
    函数作用：
        根据 provider 名称创建一个 ChatOpenAI 客户端配置。
    输入参数：
        - name: str
        - overrides: dict[str, Any]
    输出参数：
        - LLMClientConfig
    """
    cfg = PROVIDERS[name]

    if cfg["api_key_env"]:
        api_key = os.getenv(cfg["api_key_env"])
        if not api_key:
            raise RuntimeError(
                f"missing env var {cfg['api_key_env']} for provider {name!r}"
            )
    else:
        api_key = "ollama"

    base_url = os.getenv("LLM_BASE_URL_OVERRIDE") or cfg["base_url"]
    model = (
        overrides.get("model")
        or os.getenv("LLM_MODEL")
        or cfg["default_model"]
    )
    client_overrides = dict(overrides)
    client_overrides.pop("model", None)

    client = ChatOpenAI(
        base_url=base_url,
        model=model,
        api_key=api_key,
        temperature=client_overrides.pop("temperature", 0.3),
        streaming=client_overrides.pop("streaming", True),
        **client_overrides,
    )
    return LLMClientConfig(provider=name, model=model, base_url=base_url, model_route=model_route, client=client)


def _fallback_provider_names(primary: str) -> list[str]:
    """
    函数作用：
        从环境变量读取 fallback provider 列表并过滤非法或重复项。
    输入参数：
        - primary: str
    输出参数：
        - list[str]
    """
    raw = os.getenv("LLM_FALLBACK_PROVIDERS", "")
    names = []
    for item in raw.split(","):
        name = item.strip().lower()
        if not name or name == primary or name not in PROVIDERS or name in names:
            continue
        names.append(name)
    return names


def get_llm(provider: str | None = None, **overrides: Any) -> GatewayChatModel:
    """
    函数作用：
        创建带网关观测能力的 LLM 客户端。
    输入参数：
        - provider: str | None，默认值 None
        - **overrides: Any
    输出参数：
        - GatewayChatModel
    """
    trace_id = overrides.pop("trace_id", None)
    thread_id = overrides.pop("thread_id", None)
    model_route = overrides.pop("model_route", "")
    name = _resolve_provider(provider)

    clients = [_build_chat_client(name, dict(overrides), model_route=model_route)]
    for fallback_name in _fallback_provider_names(name):
        clients.append(_build_chat_client(fallback_name, dict(overrides), model_route=model_route))

    return GatewayChatModel(clients, trace_id=trace_id, thread_id=thread_id)


def supports_tools(provider: str | None = None) -> bool:
    """
    函数作用：
        待补充。
    输入参数：
        - provider: str | None，默认值 None
    输出参数：
        - bool
    """
    name = _resolve_provider(provider)
    return PROVIDERS[name]["supports_tools"]


def current_provider() -> str:
    """
    函数作用：
        待补充。
    输入参数：
        - 无
    输出参数：
        - str
    """
    return _resolve_provider(None)
