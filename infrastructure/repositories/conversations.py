"""conversations 表仓储。

兼容原有会话接口语义：标题取首条用户消息前 30 个字符，缺省 `"新对话"`，
同一 thread_id 通过数据库 upsert 保证幂等。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from infrastructure.models.base import utcnow
from infrastructure.models.conversation import (
    STATUS_ACTIVE,
    STATUS_DELETED,
    Conversation,
)
from infrastructure.repositories.base import BaseRepository

# 与旧实现保持一致的标题规则。
TITLE_MAX_LEN = 30
DEFAULT_TITLE = "新对话"


def build_title(title_seed: str | None) -> str:
    """
    函数作用：
        由首条用户消息生成会话标题，超长截断，空值回落默认标题。
    输入参数：
        - title_seed: str | None
    输出参数：
        - str
    """
    seed = (title_seed or "").strip().replace("\n", " ")
    if not seed:
        return DEFAULT_TITLE
    return seed[:TITLE_MAX_LEN]


class ConversationRepository(BaseRepository):
    """会话读写。"""

    async def get_by_id(self, conversation_id: uuid.UUID) -> Conversation | None:
        """
        函数作用：
            按主键查会话。
        输入参数：
            - conversation_id: uuid.UUID
        输出参数：
            - Conversation | None
        """
        return await self.session.get(Conversation, conversation_id)

    async def get_by_thread_id(self, thread_id: str) -> Conversation | None:
        """
        函数作用：
            按业务键 thread_id 查会话，这是 Chat API 的主要入口。
        输入参数：
            - thread_id: str
        输出参数：
            - Conversation | None
        """
        stmt = select(Conversation).where(Conversation.thread_id == thread_id).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def ensure_conversation(
        self,
        thread_id: str,
        *,
        title_seed: str | None = None,
        user_id: uuid.UUID | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Conversation:
        """
        函数作用：
            幂等地取回或创建会话。已存在且标题仍是默认值时，用新的 title_seed 补标题。
        输入参数：
            - thread_id: str
            - title_seed: str | None，默认值 None
            - user_id: uuid.UUID | None，默认值 None
            - meta: dict[str, Any] | None，默认值 None
        输出参数：
            - Conversation
        """
        existing = await self.get_by_thread_id(thread_id)
        if existing is not None:
            if title_seed and existing.title in ("", DEFAULT_TITLE):
                existing.title = build_title(title_seed)
            if user_id is not None and existing.user_id is None:
                existing.user_id = user_id
            await self.flush()
            return existing
        values = {
            "id": uuid.uuid4(),
            "thread_id": thread_id,
            "user_id": user_id,
            "title": build_title(title_seed),
            "status": STATUS_ACTIVE,
            "metadata": self._json(meta or {}),
        }
        bind = self.session.get_bind()
        dialect_name = bind.dialect.name if bind is not None else ""
        if dialect_name == "postgresql":
            stmt = postgresql_insert(Conversation.__table__).values(**values).on_conflict_do_nothing(
                index_elements=["thread_id"]
            )
            await self.session.execute(stmt)
            await self.flush()
            existing = await self.get_by_thread_id(thread_id)
            if existing is not None:
                if title_seed and existing.title in ("", DEFAULT_TITLE):
                    existing.title = build_title(title_seed)
                if user_id is not None and existing.user_id is None:
                    existing.user_id = user_id
                await self.flush()
                return existing
        conversation = Conversation(
            id=values["id"],
            thread_id=thread_id,
            user_id=user_id,
            title=values["title"],
            status=STATUS_ACTIVE,
            meta=values["metadata"],
        )
        self.session.add(conversation)
        await self.flush()
        return conversation

    async def list_recent(
        self,
        *,
        limit: int = 50,
        user_id: uuid.UUID | None = None,
        include_deleted: bool = False,
    ) -> list[Conversation]:
        """
        函数作用：
            列出最近活跃的会话，供 /api/threads 使用。按 last_message_at 倒序，
            未产生消息的会话回落到创建时间。
        输入参数：
            - limit: int，默认值 50
            - user_id: uuid.UUID | None，默认值 None 表示不按用户过滤
            - include_deleted: bool，默认值 False
        输出参数：
            - list[Conversation]
        """
        stmt = select(Conversation)
        if not include_deleted:
            stmt = stmt.where(Conversation.status != STATUS_DELETED)
        if user_id is not None:
            stmt = stmt.where(Conversation.user_id == user_id)
        stmt = stmt.order_by(
            func.coalesce(Conversation.last_message_at, Conversation.created_at).desc()
        ).limit(limit)
        return list((await self.session.execute(stmt)).scalars())

    async def touch(
        self, thread_id: str, *, message_delta: int = 0
    ) -> Conversation | None:
        """
        函数作用：
            刷新会话的最后活跃时间并累加消息计数，每轮对话结束时调用。
        输入参数：
            - thread_id: str
            - message_delta: int，默认值 0
        输出参数：
            - Conversation | None
        """
        conversation = await self.get_by_thread_id(thread_id)
        if conversation is None:
            return None
        conversation.last_message_at = utcnow()
        if message_delta:
            conversation.message_count = max(0, conversation.message_count + message_delta)
        await self.flush()
        return conversation

    async def rename(self, thread_id: str, title: str) -> Conversation | None:
        """
        函数作用：
            重命名会话，标题同样受长度上限约束。
        输入参数：
            - thread_id: str
            - title: str
        输出参数：
            - Conversation | None
        """
        conversation = await self.get_by_thread_id(thread_id)
        if conversation is None:
            return None
        conversation.title = build_title(title)
        await self.flush()
        return conversation

    async def soft_delete(self, thread_id: str) -> bool:
        """
        函数作用：
            软删除会话。保留消息与运行轨迹以便审计，只把状态置为 deleted。
        输入参数：
            - thread_id: str
        输出参数：
            - bool，True 表示确有会话被标记
        """
        conversation = await self.get_by_thread_id(thread_id)
        if conversation is None:
            return False
        conversation.status = STATUS_DELETED
        await self.flush()
        return True

    async def count(self, *, include_deleted: bool = False) -> int:
        """
        函数作用：
            统计会话总数，供后台看板使用。
        输入参数：
            - include_deleted: bool，默认值 False
        输出参数：
            - int
        """
        stmt = select(func.count(Conversation.id))
        if not include_deleted:
            stmt = stmt.where(Conversation.status != STATUS_DELETED)
        return int((await self.session.execute(stmt)).scalar() or 0)


__all__ = ["ConversationRepository", "DEFAULT_TITLE", "TITLE_MAX_LEN", "build_title"]
