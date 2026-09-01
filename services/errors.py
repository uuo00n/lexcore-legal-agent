"""跨服务边界共享的错误类型与可重试元数据。"""
from __future__ import annotations


class LegalServiceError(Exception):
    """所有可安全跨服务层传播的应用错误基类。"""

    default_code = "service_error"
    default_retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code or self.default_code
        self.retryable = self.default_retryable if retryable is None else bool(retryable)
        self.status_code = status_code


class LLMError(LegalServiceError):
    """LLM Provider 调用失败。"""

    default_code = "llm_error"


class ToolError(LegalServiceError):
    """Agent Tool 执行失败。"""

    default_code = "tool_error"


class RetrievalError(LegalServiceError):
    """本地或远程检索失败。"""

    default_code = "retrieval_error"


class DelilegalAPIError(LegalServiceError):
    """得理法律开放平台调用失败。"""

    default_code = "delilegal_api_error"


class DatabaseError(LegalServiceError):
    """数据库连接或事务失败。"""

    default_code = "database_error"


class CacheError(LegalServiceError):
    """缓存连接或命令执行失败。"""

    default_code = "cache_error"


__all__ = [
    "CacheError",
    "DatabaseError",
    "DelilegalAPIError",
    "LLMError",
    "LegalServiceError",
    "RetrievalError",
    "ToolError",
]
