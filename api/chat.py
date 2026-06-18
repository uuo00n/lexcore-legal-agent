"""聊天接口：SSE 流式 + 对话后记忆提取。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from services.checkpoint import load_doc, upsert_thread
from services.evidence_video import evidence_prompt_summary
from services.memory import load_all_messages
from services.legal_analysis import analyze_legal_message
from services.cache import get_cached_answer, set_cached_answer
from services.metrics import inc_counter, observe
from services.observability import (
    complete_trace,
    create_trace,
    new_trace_id,
    record_event,
)
from services.quota import consume_request


log = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    thread_id: str
    message: str
    doc_id: Optional[str] = None
    evidence_id: Optional[str] = None


def _sse(event: str, data: str) -> dict:
    """
    函数作用：
        待补充。
    输入参数：
        - event: str
        - data: str
    输出参数：
        - dict
    """
    return {"event": event, "data": data}


def _build_process_message(tool_names: list[str]) -> str:
    """
    函数作用：
        根据工具调用生成前端可见的处理过程文案。

        注意：这里故意不透出 AIMessage.content。部分模型会在工具调用消息中
        夹带不稳定的自由文本或推理片段，前端只应展示产品化的流程状态。
    输入参数：
        - tool_names: list[str]，本轮即将调用的工具名称
    输出参数：
        - str，面向用户的处理过程说明
    """
    normalized = {name for name in tool_names if name}
    if not normalized:
        return "正在分析问题并规划下一步处理..."
    if any("search" in name or "legal_search" in name for name in normalized):
        return "正在检索相关法条和法律依据..."
    if any("risk" in name for name in normalized):
        return "正在评估事实对应的法律风险..."
    if any("compare" in name for name in normalized):
        return "正在对比相关法律规则..."
    if any("review" in name for name in normalized):
        return "正在审查文档中的法律风险点..."
    return f"正在调用工具处理：{', '.join(tool_names)}..."


def _checkpoint_has_messages(graph, thread_id: str) -> bool:
    """
    函数作用：
        判断当前内存 checkpoint 是否已经包含该会话的消息历史。
    输入参数：
        - graph: LangGraph compiled graph
        - thread_id: str
    输出参数：
        - bool
    """
    try:
        snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
        return bool(snapshot and snapshot.values and snapshot.values.get("messages"))
    except Exception:
        return False


def _archive_item_to_message(item: dict) -> HumanMessage | AIMessage | SystemMessage | None:
    """
    函数作用：
        将持久归档中的消息字典转为 LangChain 消息对象。
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


def _load_archived_context_messages(thread_id: str) -> list[HumanMessage | AIMessage | SystemMessage]:
    """
    函数作用：
        从持久归档加载可作为模型上下文的历史消息。
    输入参数：
        - thread_id: str
    输出参数：
        - list[HumanMessage | AIMessage | SystemMessage]
    """
    messages = []
    try:
        archived = load_all_messages(thread_id)
    except Exception:
        return messages
    for item in archived:
        message = _archive_item_to_message(item)
        if message is not None:
            messages.append(message)
    return messages


def _build_state_input(
    graph,
    req: ChatRequest,
    *,
    doc_text: Optional[str],
    doc_name: Optional[str],
    trace_id: str,
) -> dict:
    """
    函数作用：
        构造 LangGraph 输入；内存 checkpoint 缺失时从持久归档恢复会话上下文。
    输入参数：
        - graph: LangGraph compiled graph
        - req: ChatRequest
        - doc_text: Optional[str]
        - doc_name: Optional[str]
        - trace_id: str
    输出参数：
        - dict
    """
    messages: list[HumanMessage | AIMessage | SystemMessage] = []
    if not _checkpoint_has_messages(graph, req.thread_id):
        messages.extend(_load_archived_context_messages(req.thread_id))
    messages.append(HumanMessage(content=req.message))
    evidence_text: Optional[str] = None
    if req.evidence_id:
        try:
            evidence_text = evidence_prompt_summary(req.evidence_id)
        except Exception as exc:
            evidence_text = f"用户提供了视频证据 evidence_id={req.evidence_id}，但读取处理报告失败：{exc}"
    return {
        "messages": messages,
        "uploaded_doc_text": doc_text,
        "uploaded_doc_name": doc_name,
        "uploaded_evidence_id": req.evidence_id,
        "uploaded_evidence_text": evidence_text,
        "thread_id": req.thread_id,
        "trace_id": trace_id,
        "retrieved_laws": [],
        "needs_follow_up": False,
        "supervisor_route": "",
        "supervisor_reason": "",
        "agent_reports": [],
        "supervisor_finalized": False,
        "viking_context": "",
        "viking_context_hits": [],
        "tool_call_count": 0,
    }


async def _event_stream(graph, req: ChatRequest) -> AsyncIterator[dict]:
    """
    函数作用：
        流式执行 LangGraph 并输出 SSE 事件。
    输入参数：
        - graph: 未标注
        - req: ChatRequest
    输出参数：
        - AsyncIterator[dict]
    """
    trace_id = new_trace_id()
    trace_started = time.perf_counter()
    trace_completed = False
    emit_done = True
    create_trace(trace_id, req.thread_id, req.message)
    record_event(trace_id, "chat_start", name="chat", payload={"doc_id": req.doc_id, "evidence_id": req.evidence_id})
    config = {"configurable": {"thread_id": req.thread_id}}

    doc_text: Optional[str] = None
    doc_name: Optional[str] = None
    if req.doc_id:
        doc = load_doc(req.doc_id)
        if doc is None:
            yield _sse("error", json.dumps({"message": f"doc_id {req.doc_id} not found"}))
            yield _sse("done", "")
            return
        doc_text = doc["text"]
        doc_name = doc["filename"]

    state_input = _build_state_input(
        graph,
        req,
        doc_text=doc_text,
        doc_name=doc_name,
        trace_id=trace_id,
    )

    try:
        yield _sse("thought", json.dumps(
            {"content": "正在分析问题、读取会话上下文并准备检索..."},
            ensure_ascii=False,
        ))
        final_content = ""
        retrieved_laws: list[dict] = []
        cached = get_cached_answer(req.message, doc_id=req.doc_id)
        if cached:
            record_event(trace_id, "cache_hit", name="response_cache")
            inc_counter("legal_response_cache_hits_total")
            final_content = cached
            chunk_size = 4
            for i in range(0, len(final_content), chunk_size):
                yield _sse("token", final_content[i:i + chunk_size])
                await asyncio.sleep(0.01)
            analysis = analyze_legal_message(req.message, final_content, retrieved_laws)
            complete_trace(
                trace_id,
                final_answer=final_content,
                status="success",
                legal_analysis=analysis,
            )
            trace_completed = True
            return
        inc_counter("legal_response_cache_misses_total")

        async for chunk in graph.astream(state_input, config, stream_mode="updates"):
            for node_name, node_output in chunk.items():
                record_event(
                    trace_id,
                    "graph_node",
                    name=node_name,
                    payload={"keys": list(node_output.keys()) if isinstance(node_output, dict) else []},
                )
                if isinstance(node_output, dict) and node_output.get("context_status"):
                    yield _sse("context_status", json.dumps(
                        node_output["context_status"],
                        ensure_ascii=False,
                    ))
                if node_name == "tools":
                    msgs = node_output.get("messages", [])
                    for m in msgs:
                        if hasattr(m, "name"):
                            record_event(trace_id, "tool_start", name=m.name or "")
                            yield _sse("tool_start", json.dumps({"name": m.name or ""}))
                            content = m.content if hasattr(m, "content") else ""
                            try:
                                parsed = json.loads(content) if isinstance(content, str) else content
                            except (json.JSONDecodeError, TypeError):
                                parsed = content
                            record_event(
                                trace_id,
                                "tool_end",
                                name=m.name or "",
                                payload={"output_preview": parsed if isinstance(parsed, dict) else str(parsed)[:1000]},
                            )
                            yield _sse("tool_end", json.dumps(
                                {"name": m.name or "", "output": parsed},
                                ensure_ascii=False,
                            ))
                elif node_name == "context_compaction":
                    if isinstance(node_output, dict) and node_output.get("context_compacted"):
                        yield _sse("thought", json.dumps(
                            {"content": "已压缩较早对话并更新实体记忆，保留最近上下文继续处理。"},
                            ensure_ascii=False,
                        ))
                elif node_name == "collect_laws":
                    retrieved_laws = node_output.get("retrieved_laws", []) or retrieved_laws
                elif node_name in {"agent", "fact_check", "fact_agent", "contract_agent", "legal_consult_agent", "supervisor_agent"}:
                    msgs = node_output.get("messages", [])
                    for m in msgs:
                        if isinstance(m, AIMessage):
                            if m.tool_calls:
                                # 工具调用阶段只展示“处理过程”，不展示模型内部推理或自由文本。
                                tc_names = [tc.get("name", "") for tc in m.tool_calls]
                                yield _sse("thought", json.dumps(
                                    {"content": _build_process_message(tc_names)},
                                    ensure_ascii=False,
                                ))
                            elif m.content and node_name == "supervisor_agent":
                                # 纯文字、无工具调用 → 最终回答
                                final_content = m.content

        # 将最终回复按块流式发送
        if final_content:
            chunk_size = 4
            for i in range(0, len(final_content), chunk_size):
                yield _sse("token", final_content[i:i + chunk_size])
                await asyncio.sleep(0.02)
        analysis = analyze_legal_message(req.message, final_content, retrieved_laws)
        set_cached_answer(req.message, final_content, doc_id=req.doc_id)
        complete_trace(
            trace_id,
            final_answer=final_content,
            status="success",
            legal_analysis=analysis,
        )
        trace_completed = True

    except (asyncio.CancelledError, GeneratorExit):
        emit_done = False
        raise
    except Exception as exc:
        log.exception("chat stream failed")
        complete_trace(trace_id, status="error", error=str(exc))
        trace_completed = True
        yield _sse("error", json.dumps({"message": str(exc)}))
    finally:
        elapsed_ms = int((time.perf_counter() - trace_started) * 1000)
        observe("legal_chat_latency_ms", elapsed_ms, {"status": "success" if trace_completed else "error"})
        inc_counter("legal_chat_requests_total")
        if not trace_completed:
            complete_trace(trace_id, status="cancelled")
        record_event(
            trace_id,
            "chat_done",
            name="chat",
            payload={"elapsed_ms": elapsed_ms},
        )
        if emit_done:
            yield _sse("done", "")


async def _async_extract_memory(thread_id: str, messages):
    """
    函数作用：
        后台异步记忆提取（由 BackgroundTasks 调度，不阻塞响应）。
    输入参数：
        - thread_id: str
        - messages: 未标注
    输出参数：
        - 未标注
    """
    try:
        from services.memory_extractor import extract_and_save_memory
        await extract_and_save_memory(thread_id, messages)
    except Exception as e:
        log.warning("记忆提取失败（不影响对话）: %s", e)


@router.post("/chat")
async def chat(req: ChatRequest, request: Request, background_tasks: BackgroundTasks):
    """
    函数作用：
        待补充。
    输入参数：
        - req: ChatRequest
        - request: Request
        - background_tasks: BackgroundTasks
    输出参数：
        - 未标注
    """
    if not req.message.strip():
        raise HTTPException(400, "message is empty")
    if not req.thread_id.strip():
        raise HTTPException(400, "thread_id is empty")

    quota = consume_request(req.thread_id)
    if not quota.allowed:
        raise HTTPException(
            429,
            {
                "message": quota.reason,
                "request_count": quota.request_count,
                "token_count": quota.token_count,
                "request_limit": quota.request_limit,
                "token_limit": quota.token_limit,
            },
        )

    upsert_thread(req.thread_id, title_seed=req.message.strip())
    graph = request.app.state.graph

    async def _wrapped_stream():
        """
        函数作用：
            待补充。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        async for event in _event_stream(graph, req):
            yield event

        # 流结束后在后台执行记忆提取，不阻塞响应
        try:
            config = {"configurable": {"thread_id": req.thread_id}}
            snapshot = await graph.aget_state(config)
            messages = snapshot.values.get("messages", [])
            if messages:
                background_tasks.add_task(_async_extract_memory, req.thread_id, messages)
        except Exception as e:
            log.warning("无法获取对话状态用于记忆提取: %s", e)

    return EventSourceResponse(_wrapped_stream())
