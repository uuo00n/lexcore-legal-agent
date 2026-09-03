"""tool_calls 表 —— Agent 工具调用明细。

`input` 与 `output_summary` 都是 JSONB：
- `input` 保存工具入参（已脱敏），不保存凭据、Header 或完整 HTTP 上下文。
- `output_summary` 只保存摘要，不落完整检索结果或裁判文书全文，避免表膨胀；
  例如 {"status": "found", "result_count": 5, "top_score": 0.71, "sources": [...]}。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.models.base import (
    Base,
    BigIntType,
    JSONBType,
    TimestampMixin,
    UUIDType,
)


class ToolCall(TimestampMixin, Base):
    """一次工具调用记录。"""

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True, autoincrement=True)

    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 运行被清理时工具明细一并删除。
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )

    # 发起调用的专家节点，例如 legal_consult_agent / statute_retrieval_agent。
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 工具名，例如 retrieve_local_law_tool / search_law_tool / search_case_tool。
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    input_payload: Mapped[dict[str, Any]] = mapped_column(
        "input", JSONBType, nullable=False, default=dict, server_default=text("'{}'")
    )
    output_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, nullable=False, default=dict, server_default=text("'{}'")
    )

    latency_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"), index=True
    )
    error: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )

    agent_run = relationship("AgentRun", back_populates="tool_calls", lazy="raise")


__all__ = ["ToolCall"]
