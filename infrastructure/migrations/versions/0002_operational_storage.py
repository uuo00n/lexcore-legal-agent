"""将辅助运行数据迁入 PostgreSQL

Revision ID: 0002_operational
Revises: 0001_initial
Create Date: 2026-09-02

所有运行表由 Alembic 统一创建；应用启动仅验证 schema，不再执行 DDL。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_operational"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("doc_id", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("doc_id", name="pk_documents"),
    )
    op.create_index("ix_documents_created_at", "documents", ["created_at"])

    op.create_table(
        "conversation_summaries",
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("msg_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("thread_id", name="pk_conversation_summaries"),
    )
    op.create_index(
        "ix_conversation_summaries_updated_at",
        "conversation_summaries",
        ["updated_at"],
    )

    op.create_table(
        "user_profiles",
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("profile", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("thread_id", name="pk_user_profiles"),
    )
    op.create_index("ix_user_profiles_updated_at", "user_profiles", ["updated_at"])

    op.create_table(
        "quota_usage",
        sa.Column("subject", sa.String(length=128), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("token_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("subject", "usage_date", name="pk_quota_usage"),
    )
    op.create_index("ix_quota_usage_updated_at", "quota_usage", ["updated_at"])

    op.create_table(
        "llm_call_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("thread_id", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("fallback_from", sa.String(length=64), nullable=False, server_default=sa.text("''")),
        sa.Column("model_route", sa.String(length=64), nullable=False, server_default=sa.text("''")),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_llm_call_logs"),
    )
    op.create_index("ix_llm_call_logs_trace", "llm_call_logs", ["trace_id", "created_at"])
    op.create_index("ix_llm_call_logs_created", "llm_call_logs", [sa.text("created_at DESC")])

    op.create_table(
        "agent_traces",
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("final_answer", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'running'")),
        sa.Column("legal_analysis", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("started_at", sa.BigInteger(), nullable=False),
        sa.Column("completed_at", sa.BigInteger(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.PrimaryKeyConstraint("trace_id", name="pk_agent_traces"),
    )
    op.create_index("ix_agent_traces_thread", "agent_traces", ["thread_id", sa.text("started_at DESC")])
    op.create_index("ix_agent_traces_started", "agent_traces", [sa.text("started_at DESC")])

    op.create_table(
        "agent_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False, server_default=sa.text("''")),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_events"),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["agent_traces.trace_id"],
            name="fk_agent_events_trace_id_agent_traces",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_agent_events_trace", "agent_events", ["trace_id", "id"])

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("mode", sa.String(length=64), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=True),
        sa.Column("num_queries", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result_path", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("details", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_eval_runs"),
    )
    op.create_index("ix_eval_runs_created", "eval_runs", [sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("ix_eval_runs_created", table_name="eval_runs")
    op.drop_table("eval_runs")
    op.drop_index("ix_agent_events_trace", table_name="agent_events")
    op.drop_table("agent_events")
    op.drop_index("ix_agent_traces_started", table_name="agent_traces")
    op.drop_index("ix_agent_traces_thread", table_name="agent_traces")
    op.drop_table("agent_traces")
    op.drop_index("ix_llm_call_logs_created", table_name="llm_call_logs")
    op.drop_index("ix_llm_call_logs_trace", table_name="llm_call_logs")
    op.drop_table("llm_call_logs")
    op.drop_index("ix_quota_usage_updated_at", table_name="quota_usage")
    op.drop_table("quota_usage")
    op.drop_index("ix_user_profiles_updated_at", table_name="user_profiles")
    op.drop_table("user_profiles")
    op.drop_index("ix_conversation_summaries_updated_at", table_name="conversation_summaries")
    op.drop_table("conversation_summaries")
    op.drop_index("ix_documents_created_at", table_name="documents")
    op.drop_table("documents")
