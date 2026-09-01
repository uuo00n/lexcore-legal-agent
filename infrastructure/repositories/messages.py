"""messages 表仓储。

写入前先锁定会话并取得最大 msg_index，再按顺序递增，保证历史回放顺序稳定。
(conversation_id, msg_index) 上的唯一约束提供最后一道并发一致性保护。
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable, Mapping

from sqlalchemy import func, select

from infrastructure.models.message import MESSAGE_ROLES, Message
from infrastructure.repositories.base import BaseRepository

# 与 services/memory.py 的估算口径保持一致，避免两处 token 预算打架。
CHARS_PER_TOKEN = 1.5


def estimate_tokens(text: str) -> int:
    """
    函数作用：
        粗估文本 token 数（中英文混排下按字符数折算）。
    输入参数：
        - text: str
    输出参数：
        - int
    """
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN) + 1


class MessageRepository(BaseRepository):
    """消息归档读写。"""

    async def next_index(self, conversation_id: uuid.UUID) -> int:
        """
        函数作用：
            返回该会话下一个可用的 msg_index。
        输入参数：
            - conversation_id: uuid.UUID
        输出参数：
            - int
        """
        stmt = select(func.max(Message.msg_index)).where(
            Message.conversation_id == conversation_id
        )
        current = (await self.session.execute(stmt)).scalar()
        return 0 if current is None else int(current) + 1

    async def append_messages(
        self,
        conversation_id: uuid.UUID,
        thread_id: str,
        items: Iterable[Mapping[str, Any]],
    ) -> list[Message]:
        """
        函数作用：
            批量追加消息。每条 item 支持 role / content / metadata / token_count，
            metadata 写前脱敏，token_count 缺省时按字符数估算。
        输入参数：
            - conversation_id: uuid.UUID
            - thread_id: str
            - items: Iterable[Mapping[str, Any]]
        输出参数：
            - list[Message]
        """
        index = await self.next_index(conversation_id)
        created: list[Message] = []
        for item in items:
            role = str(item.get("role") or "").strip()
            if role not in MESSAGE_ROLES:
                raise ValueError(f"unsupported message role: {role!r}")
            content = str(item.get("content") or "")
            raw_tokens = item.get("token_count")
            message = Message(
                conversation_id=conversation_id,
                thread_id=thread_id,
                role=role,
                content=content,
                msg_index=index,
                token_count=int(raw_tokens) if raw_tokens else estimate_tokens(content),
                meta=self._json(item.get("metadata") or {}),
            )
            self.session.add(message)
            created.append(message)
            index += 1
        await self.flush()
        return created

    async def list_by_thread(self, thread_id: str, *, limit: int | None = None) -> list[Message]:
        """
        函数作用：
            按 msg_index 升序取回消息，用于 checkpoint 为空时的历史回放。
        输入参数：
            - thread_id: str
            - limit: int | None，默认值 None 表示不限制
        输出参数：
            - list[Message]
        """
        stmt = (
            select(Message)
            .where(Message.thread_id == thread_id)
            .order_by(Message.msg_index.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self.session.execute(stmt)).scalars())

    async def list_tail(self, thread_id: str, *, limit: int = 8) -> list[Message]:
        """
        函数作用：
            取最近若干条消息（滑动窗口），返回结果仍按 msg_index 升序。
        输入参数：
            - thread_id: str
            - limit: int，默认值 8
        输出参数：
            - list[Message]
        """
        stmt = (
            select(Message)
            .where(Message.thread_id == thread_id)
            .order_by(Message.msg_index.desc())
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars())
        return list(reversed(rows))

    async def count_by_thread(self, thread_id: str) -> int:
        """
        函数作用：
            统计某会话的消息条数。
        输入参数：
            - thread_id: str
        输出参数：
            - int
        """
        stmt = select(func.count(Message.id)).where(Message.thread_id == thread_id)
        return int((await self.session.execute(stmt)).scalar() or 0)


__all__ = ["CHARS_PER_TOKEN", "MessageRepository", "estimate_tokens"]
