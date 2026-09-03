"""PostgreSQL 声明式基类与时间戳 Mixin。

设计要点：
- `JSONBType` 使用 PostgreSQL JSONB，便于结构化 Trace 查询与索引。
- `UUIDType` 使用 PostgreSQL 原生 uuid。
- `TimestampMixin` 同时给出 Python 侧 default 和数据库侧 server_default：
  前者保证 ORM 插入后立即可读（session 配置了 expire_on_commit=False），
  后者保证绕过 ORM 的原生 SQL 插入也有值。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 统一约束命名，保证 Alembic autogenerate 的 diff 稳定。
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

JSONBType = JSONB()
UUIDType = UUID(as_uuid=True)
BigIntType = BigInteger()


def utcnow() -> datetime:
    """
    函数作用：
        返回带时区的当前 UTC 时间，作为时间列的 Python 侧默认值。
    输入参数：
        - 无
    输出参数：
        - datetime
    """
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {
        dict[str, Any]: JSONBType,
        uuid.UUID: UUIDType,
        datetime: DateTime(timezone=True),
    }

    def __repr__(self) -> str:
        """
        函数作用：
            输出便于调试的模型摘要，只暴露主键，避免打印正文或 Trace 内容。
        输入参数：
            - 无
        输出参数：
            - str
        """
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk!r}>"


class TimestampMixin:
    """为模型补充 created_at / updated_at 审计列。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


def uuid_pk() -> Mapped[uuid.UUID]:
    """
    函数作用：
        构造 UUID 主键列，默认值在 Python 侧生成，便于插入前拿到 id。
    输入参数：
        - 无
    输出参数：
        - Mapped[uuid.UUID]
    """
    return mapped_column(UUIDType, primary_key=True, default=uuid.uuid4)
