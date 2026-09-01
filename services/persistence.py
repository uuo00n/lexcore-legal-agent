"""核心 PostgreSQL 持久化用例。

本模块是业务层与 ``infrastructure.repositories`` 之间的薄适配层：负责事务边界、
旧接口字段兼容和 ORM 对象序列化。上传文档、缓存、配额、摘要与画像仍由各自的
辅助存储维护；会话、消息、Agent 运行和工具调用统一从这里进入 PostgreSQL。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Iterable, Mapping

from sqlalchemy import select

from infrastructure.database import is_database_initialized, session_scope
from infrastructure.models.agent_run import RUN_SUCCESS
from infrastructure.models.conversation import Conversation
from infrastructure.repositories import (
    AgentRunRepository,
    ConversationRepository,
    MessageRepository,
    ToolCallRepository,
)


def _epoch_seconds(value: datetime | None) -> int | None:
    return int(value.timestamp()) if value is not None else None


def _normalize_role(role: str) -> str:
    return {
        "human": "user",
        "ai": "assistant",
    }.get(role, role)


def _legacy_role(role: str) -> str:
    return {
        "user": "human",
        "assistant": "ai",
    }.get(role, role)


async def ensure_conversation(
    thread_id: str,
    *,
    title_seed: str | None = None,
    user_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """幂等创建会话；数据库尚未初始化时仅供单元测试静默跳过。"""
    if not is_database_initialized():
        return None
    async with session_scope() as session:
        row = await ConversationRepository(session).ensure_conversation(
            thread_id,
            title_seed=title_seed,
            user_id=user_id,
            meta=metadata,
        )
        return {
            "id": row.id,
            "thread_id": row.thread_id,
            "title": row.title,
            "status": row.status,
        }


async def list_conversations(*, limit: int = 200) -> list[dict[str, Any]]:
    """返回与旧 ``/api/threads`` 契约兼容的会话列表。"""
    if not is_database_initialized():
        return []
    async with session_scope() as session:
        rows = await ConversationRepository(session).list_recent(limit=limit)
        return [
            {
                "thread_id": row.thread_id,
                "title": row.title,
                "created_at": _epoch_seconds(row.created_at),
                "updated_at": _epoch_seconds(row.updated_at),
            }
            for row in rows
        ]


async def delete_conversation(thread_id: str) -> bool:
    """软删除会话，保留消息与运行轨迹用于审计。"""
    if not is_database_initialized():
        return False
    async with session_scope() as session:
        return await ConversationRepository(session).soft_delete(thread_id)


async def append_messages(thread_id: str, items: Iterable[Mapping[str, Any]]) -> int:
    """按顺序追加消息，并在同一事务内更新会话计数。"""
    prepared = [
        {
            **dict(item),
            "role": _normalize_role(str(item.get("role") or "")),
        }
        for item in items
    ]
    if not prepared or not is_database_initialized():
        return 0

    async with session_scope() as session:
        conversations = ConversationRepository(session)
        conversation = await conversations.ensure_conversation(thread_id)
        # 串行化同一会话的 msg_index 分配，避免并发请求生成重复序号。
        locked = (
            await session.execute(
                select(Conversation)
                .where(Conversation.id == conversation.id)
                .with_for_update()
            )
        ).scalar_one()
        created = await MessageRepository(session).append_messages(
            locked.id,
            thread_id,
            prepared,
        )
        await conversations.touch(thread_id, message_delta=len(created))
        return len(created)


async def load_messages(thread_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """按归档顺序加载消息，并返回旧记忆层使用的 human/ai 角色名。"""
    if not is_database_initialized():
        return []
    async with session_scope() as session:
        rows = await MessageRepository(session).list_by_thread(thread_id, limit=limit)
        return [
            {
                "role": _legacy_role(row.role),
                "content": row.content,
            }
            for row in rows
        ]


async def start_agent_run(
    trace_id: str,
    thread_id: str,
    *,
    intent: str = "",
    plan: dict[str, Any] | None = None,
) -> uuid.UUID | None:
    """创建 running 状态的 Agent 运行。"""
    if not is_database_initialized():
        return None
    async with session_scope() as session:
        conversation = await ConversationRepository(session).ensure_conversation(thread_id)
        run = await AgentRunRepository(session).create_run(
            trace_id,
            thread_id,
            conversation_id=conversation.id,
            intent=intent,
            plan=plan,
        )
        return run.id


async def update_agent_run(
    trace_id: str,
    *,
    intent: str | None = None,
    plan: Any = None,
) -> bool:
    """回填 Planner 产生的结构化意图和计划。"""
    if not is_database_initialized():
        return False
    normalized_plan = None
    if plan is not None:
        normalized_plan = plan if isinstance(plan, dict) else {"steps": plan}
    async with session_scope() as session:
        row = await AgentRunRepository(session).update_plan(
            trace_id,
            normalized_plan,
            intent=intent,
        )
        return row is not None


async def finish_agent_run(
    trace_id: str,
    *,
    status: str = RUN_SUCCESS,
    error: str = "",
) -> bool:
    """把 Agent 运行推进到终态。"""
    if not is_database_initialized():
        return False
    async with session_scope() as session:
        row = await AgentRunRepository(session).complete_run(
            trace_id,
            status=status,
            error=error,
        )
        return row is not None


async def record_tool_call(
    trace_id: str,
    *,
    agent_name: str,
    tool_name: str,
    input_payload: dict[str, Any] | None = None,
    output_summary: dict[str, Any] | None = None,
    latency_ms: int = 0,
    success: bool = True,
    error: str = "",
) -> int | None:
    """保存一条已脱敏的工具调用摘要。"""
    if not is_database_initialized():
        return None
    async with session_scope() as session:
        run = await AgentRunRepository(session).get_by_trace_id(trace_id)
        row = await ToolCallRepository(session).record_call(
            trace_id,
            agent_name,
            tool_name,
            agent_run_id=run.id if run is not None else None,
            input_payload=input_payload,
            output_summary=output_summary,
            latency_ms=latency_ms,
            success=success,
            error=error,
        )
        return row.id


__all__ = [
    "append_messages",
    "delete_conversation",
    "ensure_conversation",
    "finish_agent_run",
    "list_conversations",
    "load_messages",
    "record_tool_call",
    "start_agent_run",
    "update_agent_run",
]
