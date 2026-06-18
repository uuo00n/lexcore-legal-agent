"""会话管理接口。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from services.checkpoint import (
    delete_thread,
    get_checkpointer,
    list_threads,
)
from services.answer_format import strip_answer_markdown
from services.memory import load_all_messages
from services.context_compaction import build_context_status, compact_state_context


router = APIRouter()


def _msg_to_dict(m: Any) -> dict:
    """
    函数作用：
        待补充。
    输入参数：
        - m: Any
    输出参数：
        - dict
    """
    if isinstance(m, HumanMessage):
        role = "user"
    elif isinstance(m, AIMessage):
        role = "assistant"
    elif isinstance(m, ToolMessage):
        role = "tool"
    elif isinstance(m, SystemMessage):
        role = "system"
    else:
        role = "unknown"
    content = m.content if isinstance(m.content, str) else str(m.content)
    if isinstance(m, AIMessage):
        content = strip_answer_markdown(content)
    return {
        "role": role,
        "content": content,
        "name": getattr(m, "name", None),
    }


def _visible_history_messages(messages: list[Any]) -> list[dict]:
    """
    函数作用：
        过滤会话历史中不应直接展示给用户的系统、工具和工具调用草稿消息。
    输入参数：
        - messages: list[Any]
    输出参数：
        - list[dict]
    """
    visible = []
    for m in messages:
        if isinstance(m, SystemMessage):
            continue
        if isinstance(m, ToolMessage):
            continue
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            continue
        visible.append(_msg_to_dict(m))
    return visible


def _archive_item_to_message(item: dict) -> HumanMessage | AIMessage | SystemMessage | None:
    """
    函数作用：
        将持久归档消息转为历史接口可复用的 LangChain 消息对象。
    输入参数：
        - item: dict
    输出参数：
        - HumanMessage | AIMessage | SystemMessage | None
    """
    role = item.get("role")
    content = item.get("content", "")
    if not isinstance(content, str) or not content.strip():
        return None
    if role in {"human", "user"}:
        return HumanMessage(content=content)
    if role in {"ai", "assistant"}:
        return AIMessage(content=content)
    if role == "system":
        return SystemMessage(content=content)
    return None


def _archived_history_messages(thread_id: str) -> list[dict]:
    """
    函数作用：
        从持久归档加载前端可见的会话历史。
    输入参数：
        - thread_id: str
    输出参数：
        - list[dict]
    """
    messages = []
    try:
        archived = load_all_messages(thread_id)
    except Exception:
        return []
    for item in archived:
        message = _archive_item_to_message(item)
        if message is not None:
            messages.append(message)
    return _visible_history_messages(messages)


def _history_messages_for_thread(graph, thread_id: str) -> list[dict]:
    """
    函数作用：
        优先从内存 checkpoint 读取历史；缺失时回退到持久归档。
    输入参数：
        - graph: LangGraph compiled graph
        - thread_id: str
    输出参数：
        - list[dict]
    """
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = graph.get_state(config)
        if snapshot is not None and snapshot.values:
            visible = _visible_history_messages(snapshot.values.get("messages", []))
            if visible:
                return visible
    except Exception:
        pass
    return _archived_history_messages(thread_id)


def _context_status_for_thread(graph, thread_id: str) -> dict:
    """
    函数作用：
        读取指定线程的上下文窗口使用情况。
    输入参数：
        - graph: LangGraph compiled graph
        - thread_id: str
    输出参数：
        - dict
    """
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = graph.get_state(config)
        values = snapshot.values if snapshot is not None and snapshot.values else {}
    except Exception:
        values = {}
    status = build_context_status(values.get("messages", []))
    status["thread_id"] = thread_id
    return status


async def _manual_compact_thread(graph, thread_id: str) -> dict:
    """
    函数作用：
        对指定线程的 LangGraph checkpoint 主动执行一次上下文压缩。
    输入参数：
        - graph: LangGraph compiled graph
        - thread_id: str
    输出参数：
        - dict
    """
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = graph.get_state(config)
        values = dict(snapshot.values if snapshot is not None and snapshot.values else {})
    except Exception:
        values = {}
    values.setdefault("thread_id", thread_id)
    result = await compact_state_context(values, force=True)
    if result:
        await graph.aupdate_state(config, result, as_node="context_compaction")
    status = result.get("context_status") or build_context_status(values.get("messages", []))
    status["thread_id"] = thread_id
    return {
        "thread_id": thread_id,
        "compacted": bool(result.get("context_compacted")),
        "context_status": status,
    }


@router.get("/threads")
async def list_all_threads():
    """
    函数作用：
        待补充。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    return {"threads": list_threads()}


@router.get("/threads/{thread_id}/history")
async def get_history(thread_id: str, request: Request):
    """
    函数作用：
        待补充。
    输入参数：
        - thread_id: str
        - request: Request
    输出参数：
        - 未标注
    """
    graph = request.app.state.graph
    return {"thread_id": thread_id, "messages": _history_messages_for_thread(graph, thread_id)}


@router.get("/threads/{thread_id}/context")
async def get_context(thread_id: str, request: Request):
    """
    函数作用：
        返回会话上下文窗口使用情况。
    输入参数：
        - thread_id: str
        - request: Request
    输出参数：
        - dict
    """
    graph = request.app.state.graph
    return _context_status_for_thread(graph, thread_id)


@router.post("/threads/{thread_id}/compact")
async def compact_thread(thread_id: str, request: Request):
    """
    函数作用：
        手动触发会话上下文压缩。
    输入参数：
        - thread_id: str
        - request: Request
    输出参数：
        - dict
    """
    graph = request.app.state.graph
    return await _manual_compact_thread(graph, thread_id)


@router.delete("/threads/{thread_id}")
async def remove_thread(thread_id: str):
    """
    函数作用：
        待补充。
    输入参数：
        - thread_id: str
    输出参数：
        - 未标注
    """
    cp = get_checkpointer()
    try:
        cp.delete_thread(thread_id)
    except AttributeError:
        pass
    delete_thread(thread_id)
    return {"deleted": thread_id}
