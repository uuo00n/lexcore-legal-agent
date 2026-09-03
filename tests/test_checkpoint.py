"""LangGraph checkpointer 配置与内存 fallback 测试。"""
from __future__ import annotations

import asyncio
from typing import Annotated, TypedDict

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from services.checkpoint import (
    CHECKPOINT_MEMORY,
    CHECKPOINT_POSTGRES,
    CheckpointSettings,
    checkpoint_scope,
    normalize_checkpoint_dsn,
    selector_event_loop_factory,
)


class _ConversationState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def _answer(state: _ConversationState) -> dict:
    users = [message.content for message in state["messages"] if isinstance(message, HumanMessage)]
    return {"messages": [AIMessage(content=f"已看到 {len(users)} 个用户问题：{users[-1]}")]}


def _build_test_graph(checkpointer):
    builder = StateGraph(_ConversationState)
    builder.add_node("answer", _answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder.compile(checkpointer=checkpointer)


def test_checkpoint_settings_support_memory_and_postgres():
    memory = CheckpointSettings.from_env({"CHECKPOINT_BACKEND": "memory"})
    postgres = CheckpointSettings.from_env(
        {
            "CHECKPOINT_BACKEND": "postgres",
            "DATABASE_URL": "postgresql+asyncpg://legal:secret@db:5432/legal",
        }
    )

    assert memory.backend == CHECKPOINT_MEMORY
    assert memory.dsn is None
    assert postgres.backend == CHECKPOINT_POSTGRES
    assert postgres.dsn == "postgresql://legal:secret@db:5432/legal"
    assert "secret" not in postgres.safe_dsn


def test_checkpoint_settings_reject_unknown_backend():
    with pytest.raises(ValueError, match="unsupported CHECKPOINT_BACKEND"):
        CheckpointSettings.from_env({"CHECKPOINT_BACKEND": "file"})


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("postgresql+asyncpg://u:p@db/legal", "postgresql://u:p@db/legal"),
        ("postgresql+psycopg://u:p@db/legal", "postgresql://u:p@db/legal"),
        ("postgres://u:p@db/legal", "postgresql://u:p@db/legal"),
    ],
)
def test_normalize_checkpoint_dsn(source: str, expected: str):
    assert normalize_checkpoint_dsn(source) == expected


def test_selector_event_loop_factory_returns_selector_loop():
    loop = selector_event_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


async def test_memory_backend_restores_same_thread_during_process_lifetime():
    settings = CheckpointSettings(backend=CHECKPOINT_MEMORY)
    config = {"configurable": {"thread_id": "memory-thread"}}

    async with checkpoint_scope(settings) as checkpointer:
        graph = _build_test_graph(checkpointer)
        await graph.ainvoke(
            {"messages": [HumanMessage(content="公司违法解除我怎么办？")]},
            config,
        )
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="如果我工作三年呢？")]},
            config,
        )

    user_contents = [
        message.content for message in result["messages"] if isinstance(message, HumanMessage)
    ]
    assert user_contents == ["公司违法解除我怎么办？", "如果我工作三年呢？"]
    assert result["messages"][-1].content.startswith("已看到 2 个用户问题")
