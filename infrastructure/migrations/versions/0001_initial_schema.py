"""初始化 PostgreSQL 持久化 schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-01

建 users / conversations / messages / agent_runs / tool_calls 五张表。

迁移固定面向 PostgreSQL。类型在本文件内就地定义而不从 infrastructure.models 导入，保证迁移历史被冻结、
不会随模型演进而改变含义。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB_TYPE = postgresql.JSONB()
UUID_TYPE = postgresql.UUID(as_uuid=True)
BIGINT_TYPE = sa.BigInteger()


def _timestamp_columns() -> list[sa.Column]:
    """
    函数作用：
        生成所有表共用的 created_at / updated_at 列（带时区，服务端默认 now()）。
    输入参数：
        - 无
    输出参数：
        - list[sa.Column]
    """
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def _create_users() -> None:
    """
    函数作用：
        建 users 表及其索引。
    输入参数：
        - 无
    输出参数：
        - None
    """
    op.create_table(
        "users",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("display_name", sa.String(length=128), nullable=False, server_default=sa.text("''")),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False, server_default=sa.text("'user'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("preferences", JSONB_TYPE, nullable=False, server_default=sa.text("'{}'")),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_external_id", "users", ["external_id"], unique=True)
    op.create_index("ix_users_created_at", "users", ["created_at"], unique=False)


def _create_conversations() -> None:
    """
    函数作用：
        建 conversations 表及其索引；user 删除时只解除关联。
    输入参数：
        - 无
    输出参数：
        - None
    """
    op.create_table(
        "conversations",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", UUID_TYPE, nullable=True),
        sa.Column("title", sa.String(length=128), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'active'")),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB_TYPE, nullable=False, server_default=sa.text("'{}'")),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_conversations_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_conversations_thread_id", "conversations", ["thread_id"], unique=True)
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"], unique=False)
    op.create_index(
        "ix_conversations_last_message_at", "conversations", ["last_message_at"], unique=False
    )
    op.create_index("ix_conversations_created_at", "conversations", ["created_at"], unique=False)


def _create_messages() -> None:
    """
    函数作用：
        建 messages 表；会话删除时消息级联删除，(conversation_id, msg_index) 唯一。
    输入参数：
        - 无
    输出参数：
        - None
    """
    op.create_table(
        "messages",
        sa.Column("id", BIGINT_TYPE, autoincrement=True, nullable=False),
        sa.Column("conversation_id", UUID_TYPE, nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("msg_index", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("metadata", JSONB_TYPE, nullable=False, server_default=sa.text("'{}'")),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_messages_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "conversation_id", "msg_index", name="messages_conversation_msg_index"
        ),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"], unique=False)
    op.create_index("ix_messages_thread_id", "messages", ["thread_id"], unique=False)
    op.create_index("ix_messages_created_at", "messages", ["created_at"], unique=False)


def _create_agent_runs() -> None:
    """
    函数作用：
        建 agent_runs 表；会话删除时保留运行轨迹，只解除关联。
    输入参数：
        - 无
    输出参数：
        - None
    """
    op.create_table(
        "agent_runs",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", UUID_TYPE, nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'running'")),
        sa.Column("intent", sa.String(length=64), nullable=False, server_default=sa.text("''")),
        sa.Column("plan", JSONB_TYPE, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=False, server_default=sa.text("''")),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_agent_runs"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_agent_runs_conversation_id_conversations",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_agent_runs_trace_id", "agent_runs", ["trace_id"], unique=True)
    op.create_index("ix_agent_runs_thread_id", "agent_runs", ["thread_id"], unique=False)
    op.create_index(
        "ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"], unique=False
    )
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"], unique=False)
    op.create_index("ix_agent_runs_intent", "agent_runs", ["intent"], unique=False)
    op.create_index("ix_agent_runs_started_at", "agent_runs", ["started_at"], unique=False)
    op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"], unique=False)


def _create_tool_calls() -> None:
    """
    函数作用：
        建 tool_calls 表；运行被清理时工具明细级联删除。
    输入参数：
        - 无
    输出参数：
        - None
    """
    op.create_table(
        "tool_calls",
        sa.Column("id", BIGINT_TYPE, autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("agent_run_id", UUID_TYPE, nullable=True),
        sa.Column("agent_name", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("input", JSONB_TYPE, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("output_summary", JSONB_TYPE, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("error", sa.Text(), nullable=False, server_default=sa.text("''")),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_tool_calls"),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name="fk_tool_calls_agent_run_id_agent_runs",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_tool_calls_trace_id", "tool_calls", ["trace_id"], unique=False)
    op.create_index("ix_tool_calls_agent_run_id", "tool_calls", ["agent_run_id"], unique=False)
    op.create_index("ix_tool_calls_agent_name", "tool_calls", ["agent_name"], unique=False)
    op.create_index("ix_tool_calls_tool_name", "tool_calls", ["tool_name"], unique=False)
    op.create_index("ix_tool_calls_success", "tool_calls", ["success"], unique=False)
    op.create_index("ix_tool_calls_created_at", "tool_calls", ["created_at"], unique=False)


def upgrade() -> None:
    """
    函数作用：
        按外键依赖顺序建表。
    输入参数：
        - 无
    输出参数：
        - None
    """
    _create_users()
    _create_conversations()
    _create_messages()
    _create_agent_runs()
    _create_tool_calls()


def downgrade() -> None:
    """
    函数作用：
        逆序删表。
    输入参数：
        - 无
    输出参数：
        - None
    """
    op.drop_table("tool_calls")
    op.drop_table("agent_runs")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("users")
