"""In-process checkpoint continuity and isolation tests."""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


class ConversationState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def _answer(state: ConversationState) -> dict:
    questions = [message.content for message in state["messages"] if isinstance(message, HumanMessage)]
    return {"messages": [AIMessage(content=f"当前会话共有{len(questions)}轮问题。") ]}


def _checkpoint_graph():
    builder = StateGraph(ConversationState)
    builder.add_node("answer", _answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder.compile(checkpointer=MemorySaver())


async def test_checkpoint_restores_same_thread_across_two_turns():
    graph = _checkpoint_graph()
    config = {"configurable": {"thread_id": "checkpoint-conversation"}}

    await graph.ainvoke({"messages": [HumanMessage(content="违法解除有什么补偿？")]}, config)
    result = await graph.ainvoke({"messages": [HumanMessage(content="我工作五年呢？")]}, config)

    questions = [message.content for message in result["messages"] if isinstance(message, HumanMessage)]
    assert questions == ["违法解除有什么补偿？", "我工作五年呢？"]
    assert result["messages"][-1].content == "当前会话共有2轮问题。"


async def test_checkpoint_isolates_different_threads():
    graph = _checkpoint_graph()
    await graph.ainvoke(
        {"messages": [HumanMessage(content="线程A的问题")]},
        {"configurable": {"thread_id": "thread-a"}},
    )
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="线程B的问题")]},
        {"configurable": {"thread_id": "thread-b"}},
    )

    questions = [message.content for message in result["messages"] if isinstance(message, HumanMessage)]
    assert questions == ["线程B的问题"]
