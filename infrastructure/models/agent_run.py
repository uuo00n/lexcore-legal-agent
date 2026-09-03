"""agent_runs 表 —— 一次 Agent 执行的结构化轨迹。

与旧表的差别：
- `plan` 用 JSONB 保存 Planner 节点产出的计划（步骤、专家、依赖、终止条件）。
- `intent` 独立成列，来源是 request_router / Planner 的意图判定，便于分组统计。
- `status` 收敛为 running / success / error / cancelled 四态。
- 保留 `latency_ms` 冗余列，后台看板的平均耗时不必每次做时间差聚合。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.models.base import (
    Base,
    JSONBType,
    TimestampMixin,
    UUIDType,
    uuid_pk,
)

# 运行状态取值。
RUN_RUNNING = "running"
RUN_SUCCESS = "success"
RUN_ERROR = "error"
RUN_CANCELLED = "cancelled"
AGENT_RUN_STATUSES = (RUN_RUNNING, RUN_SUCCESS, RUN_ERROR, RUN_CANCELLED)
TERMINAL_STATUSES = (RUN_SUCCESS, RUN_ERROR, RUN_CANCELLED)


class AgentRun(TimestampMixin, Base):
    """一次用户请求对应的 Agent 运行。"""

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = uuid_pk()

    # 贯穿 SSE、日志、工具调用和指标的关联键。
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # 会话被删除时保留运行轨迹，仅解除关联，便于事后审计。
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RUN_RUNNING, server_default=text("'running'"), index=True
    )
    # 例如 legal_consult / statute_retrieval / case_analysis / contract_review / smalltalk。
    intent: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=text("''"), index=True
    )

    # Planner 产出的结构化计划，写入前统一脱敏。
    plan: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, nullable=False, default=dict, server_default=text("'{}'")
    )

    started_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column()
    latency_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    error: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )

    conversation = relationship("Conversation", back_populates="agent_runs", lazy="raise")
    tool_calls = relationship(
        "ToolCall",
        back_populates="agent_run",
        lazy="raise",
        passive_deletes=True,
    )


__all__ = [
    "AgentRun",
    "AGENT_RUN_STATUSES",
    "RUN_CANCELLED",
    "RUN_ERROR",
    "RUN_RUNNING",
    "RUN_SUCCESS",
    "TERMINAL_STATUSES",
]
