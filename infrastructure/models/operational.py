"""PostgreSQL 运行数据模型。

这些模型用于 Alembic 元数据完整性；运行时同步 Agent 路径通过
``infrastructure.operational_store`` 的参数化 SQL 访问同一组表。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.models.base import Base, BigIntType, JSONBType


class Document(Base):
    __tablename__ = "documents"

    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[int] = mapped_column(BigIntType, nullable=False, index=True)


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    msg_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[int] = mapped_column(BigIntType, nullable=False, index=True)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile: Mapped[dict[str, Any]] = mapped_column(
        JSONBType,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    updated_at: Mapped[int] = mapped_column(BigIntType, nullable=False, index=True)


class QuotaUsage(Base):
    __tablename__ = "quota_usage"

    subject: Mapped[str] = mapped_column(String(128), primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    token_count: Mapped[int] = mapped_column(BigIntType, nullable=False, server_default=text("0"))
    updated_at: Mapped[int] = mapped_column(BigIntType, nullable=False, index=True)


class LlmCallLog(Base):
    __tablename__ = "llm_call_logs"
    __table_args__ = (
        Index("ix_llm_call_logs_trace", "trace_id", "created_at"),
        Index("ix_llm_call_logs_created", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True, autoincrement=True)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    thread_id: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False, server_default=text("''"))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    fallback_from: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"))
    model_route: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(BigIntType, nullable=False)


class AgentTrace(Base):
    __tablename__ = "agent_traces"
    __table_args__ = (
        Index("ix_agent_traces_thread", "thread_id", text("started_at DESC")),
        Index("ix_agent_traces_started", text("started_at DESC")),
    )

    trace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    final_answer: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'running'"))
    legal_analysis: Mapped[dict[str, Any]] = mapped_column(
        JSONBType,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    started_at: Mapped[int] = mapped_column(BigIntType, nullable=False)
    completed_at: Mapped[int | None] = mapped_column(BigIntType)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))


class AgentEvent(Base):
    __tablename__ = "agent_events"
    __table_args__ = (Index("ix_agent_events_trace", "trace_id", "id"),)

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("agent_traces.trace_id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONBType,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[int] = mapped_column(BigIntType, nullable=False)


class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (Index("ix_eval_runs_created", text("created_at DESC")),)

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(64), nullable=False)
    top_k: Mapped[int | None] = mapped_column(Integer)
    num_queries: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONBType,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    result_path: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    details: Mapped[list[Any]] = mapped_column(
        JSONBType,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    created_at: Mapped[int] = mapped_column(BigIntType, nullable=False)


__all__ = [
    "AgentEvent",
    "AgentTrace",
    "ConversationSummary",
    "Document",
    "EvalRun",
    "LlmCallLog",
    "QuotaUsage",
    "UserProfile",
]
