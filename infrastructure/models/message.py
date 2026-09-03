"""messages 表 —— 对话消息归档。

保留 `msg_index` 单调序号是为了兼容现有历史恢复逻辑：Chat API 在进程内
checkpoint 为空时按 msg_index 升序回放消息。(conversation_id, msg_index)
唯一约束保证并发写入不会产生重复序号。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.models.base import (
    Base,
    BigIntType,
    JSONBType,
    TimestampMixin,
    UUIDType,
)

# 消息角色取值，与 LangChain 消息类型对应。
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_SYSTEM = "system"
ROLE_TOOL = "tool"
MESSAGE_ROLES = (ROLE_USER, ROLE_ASSISTANT, ROLE_SYSTEM, ROLE_TOOL)


class Message(TimestampMixin, Base):
    """一条归档消息。"""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "msg_index", name="messages_conversation_msg_index"),
    )

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True, autoincrement=True)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 冗余业务键，便于按 thread_id 直接查询而不 join。
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    msg_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # 估算 token 数，供上下文预算与配额统计复用。
    token_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    # 消息级结构化附加信息（关联 trace_id、引用法条、附件 id 等）。
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONBType, nullable=False, default=dict, server_default=text("'{}'")
    )

    conversation = relationship("Conversation", back_populates="messages", lazy="raise")


__all__ = [
    "Message",
    "MESSAGE_ROLES",
    "ROLE_ASSISTANT",
    "ROLE_SYSTEM",
    "ROLE_TOOL",
    "ROLE_USER",
]
