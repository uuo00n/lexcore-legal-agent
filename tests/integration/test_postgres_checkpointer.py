"""真实 PostgreSQL 上的 LangGraph 跨实例状态恢复测试。"""
from __future__ import annotations

import os
import uuid
from typing import Annotated, TypedDict

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from services.checkpoint import CHECKPOINT_POSTGRES, CheckpointSettings, checkpoint_scope


pytestmark = pytest.mark.integration


class _ConversationState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def _context_aware_answer(state: _ConversationState) -> dict:
    questions = [
        message.content for message in state["messages"] if isinstance(message, HumanMessage)
    ]
    previous = questions[-2] if len(questions) > 1 else "无"
    return {
        "messages": [
            AIMessage(content=f"已恢复上轮问题：{previous}；本轮补充：{questions[-1]}")
        ]
    }


def _build_graph(checkpointer):
    builder = StateGraph(_ConversationState)
    builder.add_node("answer", _context_aware_answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder.compile(checkpointer=checkpointer)


async def test_same_thread_recovers_state_across_checkpointer_instances():
    dsn = os.getenv("CHECKPOINT_INTEGRATION_DSN", "").strip()
    if not dsn:
        pytest.skip("set CHECKPOINT_INTEGRATION_DSN to run PostgreSQL checkpoint integration")

    settings = CheckpointSettings(
        backend=CHECKPOINT_POSTGRES,
        dsn=dsn,
        auto_setup=True,
    )
    thread_id = f"checkpoint-it-{uuid.uuid4().hex}"
    config = {"configurable": {"thread_id": thread_id}}
    first_question = "公司违法解除我怎么办？"
    second_question = "如果我工作三年呢？"

    # 第一次应用实例写入 checkpoint。
    async with checkpoint_scope(settings) as first_checkpointer:
        first_graph = _build_graph(first_checkpointer)
        await first_graph.ainvoke(
            {"messages": [HumanMessage(content=first_question)]},
            config,
        )

    # 关闭连接并创建全新的 checkpointer/graph，模拟进程重启。
    async with checkpoint_scope(settings) as second_checkpointer:
        try:
            second_graph = _build_graph(second_checkpointer)
            result = await second_graph.ainvoke(
                {"messages": [HumanMessage(content=second_question)]},
                config,
            )
            snapshot = await second_graph.aget_state(config)

            user_contents = [
                message.content
                for message in result["messages"]
                if isinstance(message, HumanMessage)
            ]
            assert user_contents == [first_question, second_question]
            assert first_question in result["messages"][-1].content
            assert snapshot.values["messages"][-1].content == result["messages"][-1].content
        finally:
            await second_checkpointer.adelete_thread(thread_id)
