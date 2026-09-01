"""LLM Gateway 运行时包装。

网关包装 LangChain chat model，在不改变调用方接口的前提下增加：
- 调用耗时记录
- 失败日志
- 可选 fallback provider
- token 使用量字段提取
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Sequence

from services.observability import record_event, record_llm_call
from services.metrics import inc_counter, observe


@dataclass(frozen=True)
class LLMClientConfig:
    """一次可调用 LLM 客户端的元信息。"""
    provider: str
    model: str
    base_url: str
    model_route: str
    client: Any


def _extract_usage(response: Any) -> dict[str, Any]:
    """
    函数作用：
        从 LangChain AIMessage 中提取 token 使用量。
    输入参数：
        - response: Any
    输出参数：
        - dict[str, Any]
    """
    metadata = getattr(response, "response_metadata", {}) or {}
    usage = metadata.get("token_usage") or metadata.get("usage") or {}
    if not usage:
        usage = getattr(response, "usage_metadata", {}) or {}
    return dict(usage) if isinstance(usage, dict) else {}


class GatewayChatModel:
    """带观测和 fallback 的 ChatModel 轻量代理。"""

    def __init__(
        self,
        clients: Sequence[LLMClientConfig],
        *,
        trace_id: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        """
        函数作用：
            初始化网关模型代理。
        输入参数：
            - clients: Sequence[LLMClientConfig]
            - trace_id: str | None，默认值 None
            - thread_id: str | None，默认值 None
        输出参数：
            - None
        """
        if not clients:
            raise ValueError("GatewayChatModel requires at least one client")
        self._clients = list(clients)
        self._trace_id = trace_id
        self._thread_id = thread_id

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "GatewayChatModel":
        """
        函数作用：
            对每个候选模型绑定工具，并返回新的网关代理。
        输入参数：
            - tools: Sequence[Any]
            - **kwargs: Any
        输出参数：
            - GatewayChatModel
        """
        bound_clients = [
            LLMClientConfig(
                provider=item.provider,
                model=item.model,
                base_url=item.base_url,
                model_route=item.model_route,
                client=item.client.bind_tools(tools, **kwargs),
            )
            for item in self._clients
        ]
        return GatewayChatModel(bound_clients, trace_id=self._trace_id, thread_id=self._thread_id)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> "GatewayChatModel":
        """对结构化输出后的 Runnable 继续保留统一 LLM Trace 与 fallback。"""
        structured_clients = [
            LLMClientConfig(
                provider=item.provider,
                model=item.model,
                base_url=item.base_url,
                model_route=item.model_route,
                client=item.client.with_structured_output(schema, **kwargs),
            )
            for item in self._clients
        ]
        return GatewayChatModel(
            structured_clients,
            trace_id=self._trace_id,
            thread_id=self._thread_id,
        )

    async def ainvoke(self, input: Any, **kwargs: Any) -> Any:
        """
        函数作用：
            异步调用主模型；失败时按顺序尝试 fallback，并记录每次尝试。
        输入参数：
            - input: Any
            - **kwargs: Any
        输出参数：
            - Any
        """
        first_error: Exception | None = None
        primary_provider = self._clients[0].provider

        for index, item in enumerate(self._clients):
            started = time.perf_counter()
            fallback_from = "" if index == 0 else primary_provider
            try:
                response = await item.client.ainvoke(input, **kwargs)
                latency_ms = int((time.perf_counter() - started) * 1000)
                usage = _extract_usage(response)
                record_llm_call(
                    provider=item.provider,
                    model=item.model,
                    base_url=item.base_url,
                    status="success",
                    latency_ms=latency_ms,
                    trace_id=self._trace_id,
                    thread_id=self._thread_id,
                    fallback_from=fallback_from,
                    model_route=item.model_route,
                    usage=usage,
                )
                record_event(
                    self._trace_id,
                    "llm_call",
                    name=item.provider,
                    payload={
                        "thread_id": self._thread_id,
                        "model": item.model,
                        "model_route": item.model_route,
                        "latency_ms": latency_ms,
                        "token_usage": usage,
                        "success": True,
                        "retry_count": index,
                        "fallback_from": fallback_from,
                    },
                )
                observe("legal_llm_latency_ms", latency_ms, {"provider": item.provider, "status": "success"})
                inc_counter("legal_llm_calls_total", {"provider": item.provider, "status": "success", "route": item.model_route or ""})
                try:
                    from services.quota import add_token_usage
                    add_token_usage(self._thread_id, usage.get("total_tokens"))
                except Exception:
                    pass
                if fallback_from:
                    record_event(
                        self._trace_id or "",
                        "llm_fallback",
                        name=item.provider,
                        payload={"from": fallback_from, "to": item.provider, "model": item.model},
                    )
                return response
            except Exception as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                if first_error is None:
                    first_error = exc
                record_llm_call(
                    provider=item.provider,
                    model=item.model,
                    base_url=item.base_url,
                    status="error",
                    latency_ms=latency_ms,
                    trace_id=self._trace_id,
                    thread_id=self._thread_id,
                    error=str(exc),
                    fallback_from=fallback_from,
                    model_route=item.model_route,
                )
                observe("legal_llm_latency_ms", latency_ms, {"provider": item.provider, "status": "error"})
                inc_counter("legal_llm_calls_total", {"provider": item.provider, "status": "error", "route": item.model_route or ""})
                record_event(
                    self._trace_id or "",
                    "llm_error",
                    name=item.provider,
                    payload={
                        "thread_id": self._thread_id,
                        "model": item.model,
                        "model_route": item.model_route,
                        "latency_ms": latency_ms,
                        "token_usage": {},
                        "success": False,
                        "error": str(exc),
                        "retry_count": index,
                        "fallback_from": fallback_from,
                    },
                )

        raise first_error or RuntimeError("all LLM gateway attempts failed")

    def __getattr__(self, name: str) -> Any:
        """
        函数作用：
            透传未显式包装的属性到主模型，保持与 LangChain 对象兼容。
        输入参数：
            - name: str
        输出参数：
            - Any
        """
        return getattr(self._clients[0].client, name)
