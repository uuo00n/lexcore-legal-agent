"""users 表仓储。"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from infrastructure.models.user import User
from infrastructure.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    """用户读写。"""

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """
        函数作用：
            按主键查用户。
        输入参数：
            - user_id: uuid.UUID
        输出参数：
            - User | None
        """
        return await self.session.get(User, user_id)

    async def get_by_external_id(self, external_id: str) -> User | None:
        """
        函数作用：
            按外部身份标识（网关下发的 sub / 工号等）查用户。
        输入参数：
            - external_id: str
        输出参数：
            - User | None
        """
        stmt = select(User).where(User.external_id == external_id).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def ensure_user(
        self,
        external_id: str,
        *,
        display_name: str = "",
        email: str | None = None,
        role: str = "user",
    ) -> User:
        """
        函数作用：
            按 external_id 幂等地取回或创建用户，并在有新值时补齐展示信息。
        输入参数：
            - external_id: str
            - display_name: str，默认值 ""
            - email: str | None，默认值 None
            - role: str，默认值 "user"
        输出参数：
            - User
        """
        existing = await self.get_by_external_id(external_id)
        if existing is not None:
            if display_name and existing.display_name != display_name:
                existing.display_name = display_name
            if email and existing.email != email:
                existing.email = email
            await self.flush()
            return existing
        user = User(
            external_id=external_id,
            display_name=display_name,
            email=email,
            role=role,
        )
        self.session.add(user)
        await self.flush()
        return user

    async def update_preferences(
        self, user_id: uuid.UUID, preferences: dict[str, Any]
    ) -> User | None:
        """
        函数作用：
            覆盖写用户偏好。偏好里可能夹带第三方配置，写前统一脱敏。
        输入参数：
            - user_id: uuid.UUID
            - preferences: dict[str, Any]
        输出参数：
            - User | None
        """
        user = await self.get_by_id(user_id)
        if user is None:
            return None
        user.preferences = self._json(preferences)
        await self.flush()
        return user

    async def list_active(self, limit: int = 50) -> list[User]:
        """
        函数作用：
            列出启用中的用户，按创建时间倒序。
        输入参数：
            - limit: int，默认值 50
        输出参数：
            - list[User]
        """
        stmt = (
            select(User)
            .where(User.is_active.is_(True))
            .order_by(User.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())


__all__ = ["UserRepository"]
