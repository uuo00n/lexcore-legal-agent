"""基于 httpx 的得理异步 HTTP Client。"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from services.cache.delilegal import get_cached_response, set_cached_response
from services.delilegal.config import DelilegalSettings
from services.delilegal.exceptions import (
    DelilegalAuthenticationError,
    DelilegalInvalidResponseError,
    DelilegalTimeoutError,
    DelilegalUpstreamError,
)
from services.delilegal.normalizers import (
    build_case_request,
    build_law_request,
    normalize_case_response,
    normalize_law_response,
)
from services.delilegal.schemas import (
    CaseSearchInput,
    CaseSearchResponse,
    LawSearchInput,
    LawSearchResponse,
)
from services.retry import is_retryable_exception, retry_async

log = logging.getLogger("legal.delilegal")


class DelilegalClient:
    """统一处理得理 URL、认证、超时、响应校验、异常映射和安全日志。"""

    def __init__(
        self,
        settings: DelilegalSettings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.settings = settings or DelilegalSettings.from_env()
        self.settings.validate_base_url()
        self.trace_id = trace_id
        self._owns_client = http_client is None
        timeout = httpx.Timeout(
            connect=self.settings.connect_timeout,
            read=self.settings.read_timeout,
            write=self.settings.read_timeout,
            pool=self.settings.connect_timeout,
        )
        self._client = http_client or httpx.AsyncClient(
            base_url=self.settings.base_url.rstrip("/"), timeout=timeout
        )

    async def __aenter__(self) -> "DelilegalClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        self.settings.validate_credentials()
        # 得理凭据名称集中在这里；不得记录或返回这些 header。
        return {
            "Content-Type": "application/json",
            "appId": self.settings.app_id,
            "secret": self.settings.secret,
        }

    async def _send_once(self, path: str, payload: dict[str, Any]) -> Any:
        """发送一次物理请求并在重试判定前完成异常映射。"""
        try:
            response = await self._client.post(path, json=payload, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise DelilegalTimeoutError("Delilegal request timed out.") from exc
        except httpx.HTTPError as exc:
            raise DelilegalUpstreamError(
                "Delilegal transport request failed.",
                retryable=is_retryable_exception(exc),
            ) from exc

        if response.status_code in {401, 403}:
            raise DelilegalAuthenticationError(
                "Delilegal authentication failed.",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise DelilegalUpstreamError(
                f"Delilegal upstream returned HTTP {response.status_code}.",
                retryable=response.status_code >= 500,
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise DelilegalInvalidResponseError(
                "Delilegal returned a non-JSON response."
            ) from exc

    async def _post(self, endpoint_type: str, payload: dict[str, Any]) -> Any:
        # 得理是外部计费接口，先查响应缓存；Redis 不可用时 get 返回 None，照常请求上游。
        cached = await get_cached_response(endpoint_type, payload, trace_id=self.trace_id)
        if cached is not None:
            log.info(
                "delilegal_request trace_id=%s endpoint_type=%s cached=true",
                self.trace_id or "-",
                endpoint_type,
            )
            return cached
        path = self.settings.endpoint(endpoint_type)
        started = time.perf_counter()
        success = False
        result_count = 0
        error_type: str | None = None
        try:
            data = await retry_async(
                lambda: self._send_once(path, payload),
                operation_name=f"delilegal.{endpoint_type}",
            )
            success = True
            body = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(body, dict):
                for key in ("items", "list", "records", "rows", "dataList"):
                    if isinstance(body.get(key), list):
                        result_count = len(body[key])
                        break
            # 只缓存成功响应；失败与异常不写缓存，避免把一次抖动固化整个 TTL。
            await set_cached_response(endpoint_type, payload, data)
            return data
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            log.info(
                "delilegal_request trace_id=%s endpoint_type=%s latency_ms=%s success=%s result_count=%s error_type=%s",
                self.trace_id or "-",
                endpoint_type,
                latency_ms,
                success,
                result_count,
                error_type or "-",
            )

    async def search_laws(self, value: LawSearchInput) -> LawSearchResponse:
        raw = await self._post("law_search", build_law_request(value))
        try:
            return normalize_law_response(raw)
        except (TypeError, ValueError) as exc:
            raise DelilegalInvalidResponseError(
                "Delilegal law search response validation failed."
            ) from exc

    async def search_cases(self, value: CaseSearchInput) -> CaseSearchResponse:
        raw = await self._post("case_search", build_case_request(value))
        try:
            return normalize_case_response(raw)
        except (TypeError, ValueError) as exc:
            raise DelilegalInvalidResponseError(
                "Delilegal case search response validation failed."
            ) from exc
