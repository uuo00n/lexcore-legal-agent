"""仓储层公共基类。

设计约束：
- 仓储只接收外部传入的 `AsyncSession`，不自己开事务，提交交给 `session_scope()`
  或 FastAPI 依赖，避免一次请求内出现多个事务边界。
- 所有写入 JSONB / error 文本的路径都必须经过 `_json()` 与 `_text()`，
  这是「API Key 不能写数据库日志」这一要求的唯一执行点。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.sanitize import redact_mapping, redact_text


class BaseRepository:
    """仓储基类，持有会话并提供脱敏辅助方法。"""

    def __init__(self, session: AsyncSession) -> None:
        """
        函数作用：
            绑定异步会话。
        输入参数：
            - session: AsyncSession
        输出参数：
            - None
        """
        self.session = session

    @staticmethod
    def _json(value: Any) -> dict[str, Any]:
        """
        函数作用：
            把任意结构规范化为可入 JSONB 的字典，并递归剥离凭据。
        输入参数：
            - value: Any
        输出参数：
            - dict[str, Any]
        """
        return redact_mapping(value)

    @staticmethod
    def _text(value: str | None) -> str:
        """
        函数作用：
            清洗自由文本（主要是异常消息），避免把连接串或密钥写进 error 列。
        输入参数：
            - value: str | None
        输出参数：
            - str
        """
        if not value:
            return ""
        return redact_text(str(value))

    async def flush(self) -> None:
        """
        函数作用：
            把当前会话内的挂起变更推到数据库，用于取回自增主键但不结束事务。
        输入参数：
            - 无
        输出参数：
            - None
        """
        await self.session.flush()


__all__ = ["BaseRepository"]
