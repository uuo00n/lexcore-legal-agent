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

from services.checkpoint import load_doc
from services.evidence_video import evidence_prompt_summary
from services.legal_analysis import analyze_legal_message
from services.cache import get_cached_answer, set_cached_answer
from services.cache.idempotency import claim as claim_idempotency, mark_completed, release
from services.cache.rate_limit import check_rate_limit
from services.cache.session import touch_session
from services.metrics import inc_counter, observe
from services.observability import (
    complete_trace,
    create_trace,
    new_trace_id,
    record_event,
    trace_context,
)
from services.quota import consume_request
from services.persistence import (
    ensure_conversation,
    finish_agent_run,
    load_messages as load_persisted_messages,
    record_tool_call,
    start_agent_run,
    update_agent_run,
)


log = logging.getLogger(__name__)
router = APIRouter()

# 幂等标记的业务场景名，进入 Redis key 的明文部分。
IDEMPOTENCY_SCOPE = "chat"
SELF_TRACED_TOOLS = {
    "search_case_tool",
    "search_law_tool",
    "retrieve_local_law_tool",
}


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


def _summarize_tool_output(value) -> dict:
    """生成可观测性摘要，不保存正文、完整检索结果或裁判文书。"""
    if isinstance(value, dict):
        summary: dict[str, object] = {
            "type": "object",
            "keys": sorted(str(key) for key in value.keys())[:20],
        }
        for key in ("status", "result_count", "count", "total", "top_score"):
            item = value.get(key)
            if isinstance(item, (str, int, float, bool)) or item is None:
                summary[key] = item
        for key in ("results", "items", "laws", "cases", "sources"):
            item = value.get(key)
            if isinstance(item, (list, tuple)):
                summary[f"{key}_count"] = len(item)
        return summary
    if isinstance(value, (list, tuple)):
        return {"type": "list", "result_count": len(value)}
    if isinstance(value, str):
        return {"type": "text", "char_count": len(value)}
    return {"type": type(value).__name__}


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


async def _acheckpoint_has_messages(graph, thread_id: str) -> bool:
    """异步读取 checkpoint；测试替身不支持异步接口时回退同步读取。"""
    aget_state = getattr(graph, "aget_state", None)
    if aget_state is None:
        return _checkpoint_has_messages(graph, thread_id)
    try:
        snapshot = await aget_state({"configurable": {"thread_id": thread_id}})
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


def _build_state_input(
    graph,
    req: ChatRequest,
    *,
    doc_text: Optional[str],
    doc_name: Optional[str],
    trace_id: str,
    archived_items: list[dict] | None = None,
    checkpoint_has_messages: bool | None = None,
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
    has_checkpoint = (
        _checkpoint_has_messages(graph, req.thread_id)
        if checkpoint_has_messages is None
        else checkpoint_has_messages
    )
    if not has_checkpoint:
        for item in archived_items or []:
            message = _archive_item_to_message(item)
            if message is not None:
                messages.append(message)
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
        "retrieved_cases": [],
        "plan": [],
        "remaining_steps": [],
        "completed_steps": [],
        "current_step": None,
        "retry_count": 0,
        "replan_retry_count": 0,
        "verifier_retry_count": 0,
        "intent": "",
        "intent_confidence": 0.0,
        "needs_follow_up": False,
        "supervisor_route": "",
        "supervisor_reason": "",
        "agent_reports": [],
        "supervisor_finalized": False,
        "verification_result": None,
        "citations": [],
        "viking_context": "",
        "viking_context_hits": [],
        "tool_call_count": 0,
        "tool_loop_failure": None,
    }


async def _event_stream(
    graph,
    req: ChatRequest,
    trace_id: str | None = None,
) -> AsyncIterator[dict]:
    """在 FastAPI 生成的请求 Trace 上下文中执行完整 SSE 工作流。"""
    effective_trace_id = trace_id or new_trace_id()
    with trace_context(
        trace_id=effective_trace_id,
        thread_id=req.thread_id,
        node_name="fastapi.chat",
    ):
        async for event in _run_event_stream(graph, req, effective_trace_id):
            yield event


async def _run_event_stream(
    graph,
    req: ChatRequest,
    trace_id: str,
) -> AsyncIterator[dict]:
    """
    函数作用：
        流式执行 LangGraph 并输出 SSE 事件。
    输入参数：
        - graph: 未标注
        - req: ChatRequest
    输出参数：
        - AsyncIterator[dict]
    """
    trace_started = time.perf_counter()
    trace_completed = False
    run_completed = False
    emit_done = True
    create_trace(trace_id, req.thread_id, req.message)
    record_event(trace_id, "chat_start", name="chat", payload={"doc_id": req.doc_id, "evidence_id": req.evidence_id})
    # 会话元数据热层：只记活跃时间、请求计数与是否带文档，不写标题与正文。
    await touch_session(req.thread_id, trace_id=trace_id, has_document=bool(req.doc_id))
    config = {
        "configurable": {"thread_id": req.thread_id, "trace_id": trace_id},
        "metadata": {"thread_id": req.thread_id, "trace_id": trace_id},
    }

    doc_text: Optional[str] = None
    doc_name: Optional[str] = None
    if req.doc_id:
        doc = load_doc(req.doc_id)
        if doc is None:
            yield _sse("error", json.dumps({"message": f"doc_id {req.doc_id} not found"}))
            complete_trace(trace_id, status="error", error="document not found")
            yield _sse("done", "")
            return
        doc_text = doc["text"]
        doc_name = doc["filename"]

    checkpoint_has_messages = await _acheckpoint_has_messages(graph, req.thread_id)
    archived_items = None
    if not checkpoint_has_messages:
        archived_items = await load_persisted_messages(req.thread_id)
    state_input = _build_state_input(
        graph,
        req,
        doc_text=doc_text,
        doc_name=doc_name,
        trace_id=trace_id,
        archived_items=archived_items,
        checkpoint_has_messages=checkpoint_has_messages,
    )
    await start_agent_run(trace_id, req.thread_id)
    pending_tool_calls: dict[str, dict] = {}

    try:
        yield _sse("thought", json.dumps(
            {"content": "正在分析问题、读取会话上下文并准备检索..."},
            ensure_ascii=False,
        ))
        final_content = ""
        retrieved_laws: list[dict] = []
        cached = get_cached_answer(req.message, doc_id=req.doc_id, trace_id=trace_id)
        if cached:
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
            await finish_agent_run(trace_id, status="success")
            run_completed = True
            trace_completed = True
            return
        inc_counter("legal_response_cache_misses_total")

        async for chunk in graph.astream(state_input, config, stream_mode="updates"):
            for node_name, node_output in chunk.items():
                if isinstance(node_output, dict) and (
                    node_output.get("plan") is not None or node_output.get("intent")
                ):
                    await update_agent_run(
                        trace_id,
                        intent=node_output.get("intent"),
                        plan=node_output.get("plan"),
                    )
                if isinstance(node_output, dict) and node_output.get("context_status"):
                    yield _sse("context_status", json.dumps(
                        node_output["context_status"],
                        ensure_ascii=False,
                    ))
                if node_name in {
                    "case_analysis_tools",
                    "statute_retrieval_tools",
                    "legal_consult_tools",
                    "tool_limit_exceeded",
                }:
                    msgs = node_output.get("messages", [])
                    for m in msgs:
                        if hasattr(m, "name"):
                            yield _sse("tool_start", json.dumps({"name": m.name or ""}))
                            content = m.content if hasattr(m, "content") else ""
                            try:
                                parsed = json.loads(content) if isinstance(content, str) else content
                            except (json.JSONDecodeError, TypeError):
                                parsed = content
                            call_id = str(getattr(m, "tool_call_id", "") or "")
                            pending = pending_tool_calls.pop(call_id, {})
                            error = ""
                            if isinstance(parsed, dict) and parsed.get("error"):
                                error = str(parsed.get("error"))
                            success = getattr(m, "status", None) != "error" and not error
                            started = float(pending.get("started", time.perf_counter()))
                            if str(m.name or "") not in SELF_TRACED_TOOLS:
                                record_event(
                                    trace_id,
                                    "tool_end",
                                    name=m.name or "",
                                    payload={
                                        **_summarize_tool_output(parsed),
                                        "success": success,
                                        "error": error,
                                        "latency_ms": max(
                                            0,
                                            int((time.perf_counter() - started) * 1000),
                                        ),
                                        "node_name": node_name,
                                        "agent_name": str(pending.get("agent_name") or node_name),
                                    },
                                )
                            await record_tool_call(
                                trace_id,
                                agent_name=str(pending.get("agent_name") or node_name),
                                tool_name=str(pending.get("tool_name") or m.name or "unknown"),
                                input_payload=pending.get("input_payload") or {},
                                output_summary=_summarize_tool_output(parsed),
                                latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
                                success=success,
                                error=error,
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
                elif node_name in {"collect_case_evidence", "collect_statute_evidence", "collect_consult_evidence"}:
                    retrieved_laws = node_output.get("retrieved_laws", []) or retrieved_laws
                elif node_name in {"agent", "fact_check", "fact_agent", "case_analysis_agent", "statute_retrieval_agent", "contract_agent", "legal_consult_agent", "request_router", "supervisor_agent", "verifier", "answer_generator"}:
                    msgs = node_output.get("messages", [])
                    for m in msgs:
                        if isinstance(m, AIMessage):
                            if m.tool_calls:
                                # 工具调用阶段只展示“处理过程”，不展示模型内部推理或自由文本。
                                tc_names = [tc.get("name", "") for tc in m.tool_calls]
                                for tc in m.tool_calls:
                                    call_id = str(tc.get("id") or f"{node_name}:{len(pending_tool_calls)}")
                                    tool_name = str(tc.get("name") or "")
                                    pending_tool_calls[call_id] = {
                                        "agent_name": node_name,
                                        "tool_name": tool_name,
                                        "input_payload": tc.get("args") or {},
                                        "started": time.perf_counter(),
                                    }
                                    if tool_name not in SELF_TRACED_TOOLS:
                                        record_event(
                                            trace_id,
                                            "tool_start",
                                            name=tool_name,
                                            payload={
                                                "node_name": node_name,
                                                "agent_name": node_name,
                                                "input_keys": sorted(
                                                    str(key)
                                                    for key in (tc.get("args") or {}).keys()
                                                )[:20],
                                            },
                                        )
                                yield _sse("thought", json.dumps(
                                    {"content": _build_process_message(tc_names)},
                                    ensure_ascii=False,
                                ))
                            elif m.content and node_name in {"request_router", "supervisor_agent", "verifier", "answer_generator"}:
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
        await finish_agent_run(trace_id, status="success")
        run_completed = True
        trace_completed = True

    except (asyncio.CancelledError, GeneratorExit):
        emit_done = False
        raise
    except Exception as exc:
        log.exception("chat stream failed")
        complete_trace(trace_id, status="error", error=str(exc))
        await finish_agent_run(trace_id, status="error", error=str(exc))
        run_completed = True
        trace_completed = True
        yield _sse("error", json.dumps({"message": str(exc)}))
    finally:
        elapsed_ms = int((time.perf_counter() - trace_started) * 1000)
        observe("legal_chat_latency_ms", elapsed_ms, {"status": "success" if trace_completed else "error"})
        inc_counter("legal_chat_requests_total")
        if not trace_completed:
            complete_trace(trace_id, status="cancelled")
        if not run_completed:
            await finish_agent_run(trace_id, status="cancelled")
        record_event(
            trace_id,
            "chat_done",
            name="chat",
            payload={"elapsed_ms": elapsed_ms},
        )
        if emit_done:
            yield _sse("done", "")


async def _async_extract_memory(thread_id: str, messages, user_id: str | None = None):
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
        await extract_and_save_memory(thread_id, messages, user_id=user_id)
    except Exception as e:
        log.warning("记忆提取失败（不影响对话）: %s", e)


@router.post("/chat")
async def chat(req: ChatRequest, request: Request, background_tasks: BackgroundTasks):
    """
    函数作用：
        受理一次对话请求：突发限流、每日配额、幂等校验后返回 SSE 流。
        限流与幂等都建立在 Redis 上，Redis 不可用时二者自动放行，
        由 SQLite 每日配额继续兜底，主链不会因缓存层故障而不可用。
    输入参数：
        - req: ChatRequest
        - request: Request，用于读取 Idempotency-Key 请求头
        - background_tasks: BackgroundTasks
    输出参数：
        - 未标注
    """
    if not req.message.strip():
        raise HTTPException(400, "message is empty")
    if not req.thread_id.strip():
        raise HTTPException(400, "thread_id is empty")

    limit = await check_rate_limit(req.thread_id, scope="chat")
    if not limit.allowed:
        raise HTTPException(
            429,
            {
                "message": limit.reason,
                "limit": limit.limit,
                "window_seconds": limit.window_seconds,
                "retry_after": limit.retry_after,
            },
            headers={"Retry-After": str(limit.retry_after)},
        )

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

    # 客户端重试与断线重连会重复 POST 同一轮对话；只有显式带 Idempotency-Key
    # 才做幂等拦截，避免把用户真正的重复提问误判为重放。
    idempotency_token = (request.headers.get("Idempotency-Key") or "").strip()
    idempotency_claim = await claim_idempotency(IDEMPOTENCY_SCOPE, idempotency_token)
    if idempotency_claim.duplicate:
        raise HTTPException(
            409,
            {
                "message": "该请求已在处理或已完成，请勿重复提交。",
                "state": (idempotency_claim.record or {}).get("state", ""),
            },
        )

    await ensure_conversation(req.thread_id, title_seed=req.message.strip())
    graph = request.app.state.graph

    async def _wrapped_stream():
        """
        函数作用：
            包装 SSE 事件流：流结束后落幂等完成标记并触发后台记忆提取。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        try:
            async for event in _event_stream(
                graph,
                req,
                trace_id=getattr(request.state, "trace_id", None),
            ):
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断开时不能再 await，否则生成器关闭会抛 RuntimeError；
            # in_progress 标记留给 TTL 自然过期。
            raise
        except BaseException:
            # 未跑完的请求不能留下 in_progress 标记，否则重试会被 409 挡住。
            await release(IDEMPOTENCY_SCOPE, idempotency_token)
            raise
        else:
            await mark_completed(IDEMPOTENCY_SCOPE, idempotency_token)

        # 流结束后在后台执行记忆提取，不阻塞响应
        try:
            config = {"configurable": {"thread_id": req.thread_id}}
            snapshot = await graph.aget_state(config)
            messages = snapshot.values.get("messages", [])
            if messages:
                background_tasks.add_task(
                    _async_extract_memory,
                    req.thread_id,
                    messages,
                    snapshot.values.get("user_id"),
                )
        except Exception as e:
            log.warning("无法获取对话状态用于记忆提取: %s", e)

    return EventSourceResponse(_wrapped_stream())
