"""users 表 —— 平台用户与偏好。

当前产品还没有登录系统，配额与会话仍以 thread_id 近似用户；本表先建立稳定的
用户主体，`external_id` 预留给后续接入的真实身份（OIDC sub、企业工号等）。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.models.base import Base, JSONBType, TimestampMixin, uuid_pk


class User(TimestampMixin, Base):
    """平台用户。"""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()

    # 外部身份标识；未接入登录系统时为空，接入后唯一。
    external_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", server_default=text("''")
    )
    email: Mapped[str | None] = mapped_column(String(320), unique=True)

    # 'user' | 'admin'，与 ADMIN_API_KEY 鉴权解耦，仅描述业务角色。
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, default="user", server_default=text("'user'")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # 结构化偏好（关注领域、回答风格等）；写入前统一脱敏，不存凭据。
    preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, nullable=False, default=dict, server_default=text("'{}'")
    )

    conversations = relationship(
        "Conversation",
        back_populates="user",
        lazy="raise",
        passive_deletes=True,
    )


__all__ = ["User"]
