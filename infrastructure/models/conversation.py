"""conversations 表 —— 对话会话。

`thread_id` 是与 LangGraph checkpoint、配额、缓存和现有前端契约共用的业务键，
因此保留为唯一索引；`id` 只作为关系型主键，供 messages / agent_runs 外键引用。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.models.base import Base, JSONBType, TimestampMixin, UUIDType, uuid_pk

# 会话状态取值。
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
STATUS_DELETED = "deleted"
CONVERSATION_STATUSES = (STATUS_ACTIVE, STATUS_ARCHIVED, STATUS_DELETED)


class Conversation(TimestampMixin, Base):
    """一次多轮法律咨询会话。"""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = uuid_pk()

    # LangGraph configurable.thread_id，跨模块的业务主键。
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    # 无登录系统时为空；接入后回填。用户删除时会话保留但解除关联。
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    title: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", server_default=text("''")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STATUS_ACTIVE, server_default=text("'active'")
    )

    # 冗余计数与时间戳，供会话列表排序，避免每次 COUNT/MAX 子查询。
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_message_at: Mapped[datetime | None] = mapped_column(index=True)

    # 会话级结构化上下文（来源渠道、上传文档 id、案件画像摘要等）。
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONBType, nullable=False, default=dict, server_default=text("'{}'")
    )

    user = relationship("User", back_populates="conversations", lazy="raise")
    messages = relationship(
        "Message",
        back_populates="conversation",
        lazy="raise",
        passive_deletes=True,
    )
    agent_runs = relationship(
        "AgentRun",
        back_populates="conversation",
        lazy="raise",
        passive_deletes=True,
    )


__all__ = [
    "Conversation",
    "CONVERSATION_STATUSES",
    "STATUS_ACTIVE",
    "STATUS_ARCHIVED",
    "STATUS_DELETED",
]
