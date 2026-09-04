"""Agent 可观测性存储层。

后台时间线、LLM 日志和评测历史统一写入 PostgreSQL，所有写入必须先脱敏。
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from collections.abc import Iterator
from typing import Any, Optional

from infrastructure.operational_store import get_operational_store
from infrastructure.sanitize import redact, redact_text


@dataclass(frozen=True)
class TraceContext:
    """跨 FastAPI、LangGraph、Tool、RAG 与 LLM 传播的请求上下文。"""

    trace_id: str = ""
    thread_id: str = ""
    node_name: str = ""
    agent_name: str = ""
    tool_name: str = ""
    model: str = ""
    retry_count: int = 0


_TRACE_CONTEXT: ContextVar[TraceContext] = ContextVar(
    "legal_trace_context",
    default=TraceContext(),
)


def get_trace_context() -> TraceContext:
    """返回当前异步任务所处的 Trace 上下文。"""
    return _TRACE_CONTEXT.get()


def set_trace_context(**values: Any) -> Token[TraceContext]:
    """合并并设置 Trace 上下文，返回可用于恢复上层上下文的 token。"""
    current = get_trace_context()
    updates = {
        key: value
        for key, value in values.items()
        if key in TraceContext.__dataclass_fields__ and value is not None
    }
    return _TRACE_CONTEXT.set(replace(current, **updates))


def reset_trace_context(token: Token[TraceContext]) -> None:
    """恢复进入当前组件前的 Trace 上下文。"""
    _TRACE_CONTEXT.reset(token)


@contextmanager
def trace_context(**values: Any) -> Iterator[TraceContext]:
    """在一个同步或异步调用片段内绑定统一 Trace 上下文。"""
    token = set_trace_context(**values)
    try:
        yield get_trace_context()
    finally:
        reset_trace_context(token)


def _retrieval_count(payload: dict[str, Any]) -> int:
    for key in ("retrieval_count", "result_count", "count"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, value)
    hits = payload.get("hits")
    return len(hits) if isinstance(hits, list) else 0


def _standard_event_payload(
    event_type: str,
    name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """为所有事件补齐统一字段；只保留元数据，不主动采集提示词或文档正文。"""
    context = get_trace_context()
    error = payload.get("error") or payload.get("error_type") or ""
    success = payload.get("success")
    if success is None:
        success = False if error or event_type.endswith("_error") else True
    latency = payload.get("latency_ms", payload.get("elapsed_ms", 0))
    try:
        latency = max(0, float(latency or 0))
    except (TypeError, ValueError):
        latency = 0
    retry_count = payload.get("retry_count", context.retry_count)
    try:
        retry_count = max(0, int(retry_count or 0))
    except (TypeError, ValueError):
        retry_count = 0
    cache_hit = payload.get("cache_hit")
    if cache_hit is None and event_type in {"cache_hit", "cache_miss"}:
        cache_hit = event_type == "cache_hit"
    tool_name = payload.get("tool_name") or context.tool_name
    if not tool_name and event_type.startswith("tool_"):
        tool_name = name
    model = payload.get("model") or context.model
    return {
        **payload,
        "thread_id": payload.get("thread_id") or context.thread_id,
        "node_name": payload.get("node_name") or context.node_name,
        "agent_name": payload.get("agent_name") or context.agent_name,
        "tool_name": tool_name,
        "model": model,
        "latency_ms": latency,
        "token_usage": payload.get("token_usage") or {},
        "success": bool(success),
        "error": str(error),
        "retrieval_count": _retrieval_count(payload),
        "retry_count": retry_count,
        "cache_hit": cache_hit,
    }


def _json_dumps(value: Any) -> str:
    """
    函数作用：
        将任意可 JSON 序列化对象转为数据库中的文本格式。
    输入参数：
        - value: Any
    输出参数：
        - str
    """
    return json.dumps(redact(value), ensure_ascii=False, default=str)


def _json_loads(value: Any, default: Any) -> Any:
    """
    函数作用：
        从数据库文本字段恢复 JSON，失败时返回默认值。
    输入参数：
        - value: str | None
        - default: Any
    输出参数：
        - Any
    """
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def new_trace_id() -> str:
    """
    函数作用：
        生成短而唯一的 trace id，便于界面展示和日志关联。
    输入参数：
        - 无
    输出参数：
        - str
    """
    return uuid.uuid4().hex[:16]


def create_trace(trace_id: str, thread_id: str, user_message: str) -> None:
    """
    函数作用：
        创建一次 Agent 运行轨迹。
    输入参数：
        - trace_id: str
        - thread_id: str
        - user_message: str
    输出参数：
        - 无
    """
    now = int(time.time() * 1000)
    get_operational_store().create_trace(
        {
            "trace_id": trace_id,
            "thread_id": thread_id,
            "user_message": redact_text(user_message),
            "started_at": now,
        }
    )


def complete_trace(
    trace_id: str,
    *,
    final_answer: str = "",
    status: str = "success",
    legal_analysis: Optional[dict[str, Any]] = None,
    error: str = "",
) -> None:
    """
    函数作用：
        完成一次 Agent 运行轨迹并记录耗时。
    输入参数：
        - trace_id: str
        - final_answer: str，默认值 ''
        - status: str，默认值 'success'
        - legal_analysis: Optional[dict[str, Any]]，默认值 None
        - error: str，默认值 ''
    输出参数：
        - 无
    """
    now = int(time.time() * 1000)
    get_operational_store().complete_trace(
        trace_id,
        {
            "final_answer": redact_text(final_answer),
            "status": status,
            "legal_analysis": _json_dumps(legal_analysis or {}),
            "completed_at": now,
            "error": redact_text(error),
        },
    )


def record_event(
    trace_id: str | None,
    event_type: str,
    *,
    name: str = "",
    payload: Optional[dict[str, Any]] = None,
) -> None:
    """
    函数作用：
        记录 Agent 执行过程中的一个事件。
    输入参数：
        - trace_id: str
        - event_type: str
        - name: str，默认值 ''
        - payload: Optional[dict[str, Any]]，默认值 None
    输出参数：
        - 无
    """
    effective_trace_id = trace_id or get_trace_context().trace_id
    if not effective_trace_id:
        return
    normalized_payload = _standard_event_payload(event_type, name, payload or {})
    get_operational_store().add_event(
        {
            "trace_id": effective_trace_id,
            "event_type": event_type,
            "name": redact_text(name),
            "payload": _json_dumps(normalized_payload),
            "created_at": int(time.time() * 1000),
        }
    )


def record_llm_call(
    *,
    provider: str,
    model: str,
    base_url: str,
    status: str,
    latency_ms: int,
    trace_id: str | None = None,
    thread_id: str | None = None,
    error: str = "",
    fallback_from: str = "",
    model_route: str = "",
    usage: Optional[dict[str, Any]] = None,
) -> None:
    """
    函数作用：
        记录一次 LLM 调用尝试。
    输入参数：
        - provider: str
        - model: str
        - base_url: str
        - status: str
        - latency_ms: int
        - trace_id: str | None，默认值 None
        - thread_id: str | None，默认值 None
        - error: str，默认值 ''
        - fallback_from: str，默认值 ''
        - usage: Optional[dict[str, Any]]，默认值 None
    输出参数：
        - 无
    """
    usage = usage or {}
    context = get_trace_context()
    trace_id = trace_id or context.trace_id or None
    thread_id = thread_id or context.thread_id or None
    get_operational_store().add_llm_call(
        {
            "trace_id": trace_id,
            "thread_id": thread_id,
            "provider": redact_text(provider),
            "model": redact_text(model),
            "base_url": redact_text(base_url),
            "status": status,
            "latency_ms": latency_ms,
            "error": redact_text(error),
            "fallback_from": redact_text(fallback_from),
            "model_route": redact_text(model_route),
            "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens"),
            "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "created_at": int(time.time() * 1000),
        }
    )


def record_eval_run(results: dict[str, Any], result_path: str = "") -> None:
    """
    函数作用：
        将一次评测结果写入历史表。
    输入参数：
        - results: dict[str, Any]
        - result_path: str，默认值 ''
    输出参数：
        - 无
    """
    get_operational_store().add_eval_run(
        {
            "mode": results.get("mode", "unknown"),
            "top_k": results.get("top_k"),
            "num_queries": results.get("num_queries", 0),
            "metrics": _json_dumps(results.get("aggregated", {})),
            "result_path": result_path,
            "details": _json_dumps(results.get("details", [])),
            "created_at": int(time.time() * 1000),
        }
    )


def dashboard_summary() -> dict[str, Any]:
    """
    函数作用：
        汇总后台看板顶部指标。
    输入参数：
        - 无
    输出参数：
        - dict[str, Any]
    """
    row = get_operational_store().dashboard_summary()
    total_traces = int(row["total_traces"])
    success_traces = int(row["success_traces"])
    return {
        "total_traces": total_traces,
        "success_rate": (success_traces / total_traces) if total_traces else 0.0,
        "avg_trace_latency_ms": int(row["avg_latency"] or 0),
        "llm_calls": int(row["llm_calls"]),
        "failed_llm_calls": int(row["failed_llm_calls"]),
        "fallback_count": int(row["fallback_count"]),
        "routed_count": int(row["routed_count"]),
        "eval_runs": int(row["eval_count"]),
    }


def list_traces(limit: int = 50) -> list[dict[str, Any]]:
    """
    函数作用：
        查询最近的 Agent 运行轨迹。
    输入参数：
        - limit: int，默认值 50
    输出参数：
        - list[dict[str, Any]]
    """
    rows = get_operational_store().list_traces(limit)
    return [
        {
            **row,
            "legal_analysis": _json_loads(row.get("legal_analysis"), {}),
        }
        for row in rows
    ]


def get_trace(trace_id: str) -> Optional[dict[str, Any]]:
    """
    函数作用：
        查询单条 trace 及其事件。
    输入参数：
        - trace_id: str
    输出参数：
        - Optional[dict[str, Any]]
    """
    traces = [item for item in list_traces(limit=200) if item["trace_id"] == trace_id]
    if not traces:
        return None
    rows = get_operational_store().list_events(trace_id)
    trace = traces[0]
    trace["events"] = [
        {
            **row,
            "payload": _json_loads(row.get("payload"), {}),
        }
        for row in rows
    ]
    return trace


def list_llm_calls(limit: int = 50) -> list[dict[str, Any]]:
    """
    函数作用：
        查询最近 LLM 调用日志。
    输入参数：
        - limit: int，默认值 50
    输出参数：
        - list[dict[str, Any]]
    """
    return get_operational_store().list_llm_calls(limit)


def list_eval_runs(limit: int = 20) -> list[dict[str, Any]]:
    """
    函数作用：
        查询最近评测历史。
    输入参数：
        - limit: int，默认值 20
    输出参数：
        - list[dict[str, Any]]
    """
    rows = get_operational_store().list_eval_runs(limit)
    return [
        {
            **row,
            "metrics": _json_loads(row.get("metrics"), {}),
            "details": _json_loads(row.get("details"), []),
        }
        for row in rows
    ]


_TIMELINE_LABELS = {
    # 请求边界与既有节点
    "chat_start": "用户提交问题",
    "supervisor_route": "中控智能体路由",
    "fact_check": "事实完整性检查",
    "contract_agent": "合同审查智能体",
    "case_analysis_agent": "案件分析智能体",
    "statute_retrieval_agent": "法规检索智能体",
    "case_retrieval_agent": "类案检索智能体",
    "legal_consult_agent": "法律咨询智能体",
    "graph_node": "LangGraph 节点执行",
    "model_route": "模型路由决策",
    "case_retrieval": "相似法律场景检索",
    "agent_tool_request": "Agent 请求工具",
    "agent_report": "专家智能体报告",
    "tool_start": "工具调用开始",
    "tool_end": "工具调用完成",
    "retrieval_collect": "法条检索收集",
    "vector_hits": "向量检索命中",
    "bm25_hits": "BM25 检索命中",
    "fused_hits": "RRF 融合命中",
    "reranker_hits": "Reranker 精排命中",
    "rag_retrieval": "RAG 检索完成",
    "cache_hit": "缓存命中",
    "cache_miss": "缓存未命中",
    "citation_guard": "法条引用校验",
    "llm_call": "LLM 调用完成",
    "llm_error": "LLM 调用失败",
    "llm_fallback": "LLM Fallback",
    "final_answer": "生成最终回答",
    "chat_done": "请求完成",
    # 重构后的工作流节点（§二十四：每个决策点都要能在时间线上看到）
    "query_rewrite": "查询改写",
    "fact_analysis": "事实充分性分析",
    "clarification_required": "发起澄清补问",
    "clarification_resumed": "澄清回复合并",
    "complexity_route": "复杂度路由决策",
    "plan_created": "生成执行计划",
    "planner_degraded": "Planner 降级兜底",
    "plan_step_started": "计划步骤开始",
    "plan_step_completed": "计划步骤完成",
    "plan_step_retry": "计划步骤重试",
    "plan_step_failed": "计划步骤失败",
    "tool_loop_stopped": "工具循环提前停止",
    "evidence_normalized": "证据归一化完成",
    "evidence_deduplicated": "证据去重",
    "verification_complete": "结果核验完成",
    "verification_issue": "核验问题",
    "verification_degraded": "核验降级",
    "verifier_llm_skipped": "语义核验跳过",
    "repair_started": "局部修复开始",
    "repair_skipped": "局部修复跳过",
    "replan_skipped": "整体重排跳过",
    "answer_citation_rejected": "答复引用未通过",
    "agent_fallback": "Agent 降级兜底",
    "context_build": "模型上下文装配",
    "context_status": "上下文预算状态",
    "context_compaction": "上下文压缩",
    "viking_context_retrieval": "OpenViking 上下文检索",
    "rate_limited": "触发限流",
}


def get_trace_timeline(trace_id: str) -> Optional[dict[str, Any]]:
    """
    函数作用：
        将 trace 事件整理成适合前端时间线展示的结构。
    输入参数：
        - trace_id: str
    输出参数：
        - Optional[dict[str, Any]]
    """
    trace = get_trace(trace_id)
    if trace is None:
        return None
    labels = _TIMELINE_LABELS
    timeline = []
    for event in trace.get("events", []):
        payload = event.get("payload", {})
        timeline.append({
            "id": event["id"],
            "time": event["created_at"],
            "type": event["event_type"],
            "title": labels.get(event["event_type"], event["event_type"]),
            "name": event.get("name", ""),
            "summary": _summarize_event(event["event_type"], payload),
            "payload": payload,
        })
    return {"trace": trace, "timeline": timeline}


def _summarize_event(event_type: str, payload: dict[str, Any]) -> str:
    """
    函数作用：
        为 trace 事件生成一句话摘要。
    输入参数：
        - event_type: str
        - payload: dict[str, Any]
    输出参数：
        - str
    """
    if event_type == "model_route":
        return f"{payload.get('route')} · {payload.get('reason')} · score={payload.get('complexity_score')}"
    if event_type == "supervisor_route":
        return f"{payload.get('route')} · {payload.get('reason')}"
    if event_type == "fact_check":
        return payload.get("reason", "")
    if event_type == "contract_agent":
        return payload.get("download_url") or payload.get("message", "")
    if event_type == "case_retrieval":
        return f"命中 {len(payload.get('cases', []))} 个相似场景"
    if event_type == "agent_tool_request":
        return "工具：" + "、".join(payload.get("tools", []))
    if event_type == "agent_report":
        return f"{payload.get('status', '')} · 检索法条 {payload.get('retrieved_law_count', 0)} 条"
    if event_type == "retrieval_collect":
        return f"收集到 {payload.get('law_count', 0)} 条法条"
    if event_type in {"vector_hits", "bm25_hits", "fused_hits", "reranker_hits"}:
        return f"命中 {len(payload.get('hits', []))} 条"
    if event_type == "rag_retrieval":
        return (
            f"命中 {payload.get('retrieval_count', 0)} 条 · "
            f"{payload.get('latency_ms', 0)} ms"
        )
    if event_type in {"cache_hit", "cache_miss"}:
        return f"{payload.get('namespace', '')} · cache_hit={payload.get('cache_hit')}"
    if event_type == "citation_guard":
        return "回答引用已调整" if payload.get("changed") else "回答引用通过"
    if event_type == "final_answer":
        return payload.get("content_preview", "")
    if event_type == "chat_done":
        return f"{payload.get('elapsed_ms', 0)} ms"
    if event_type == "llm_fallback":
        return f"{payload.get('from')} -> {payload.get('to')}"
    if event_type == "llm_call":
        usage = payload.get("token_usage") or {}
        total = usage.get("total_tokens") or usage.get("total_token_count") or 0
        return f"{payload.get('model', '')} · {payload.get('latency_ms', 0)} ms · {total} tokens"
    if event_type == "llm_error":
        return payload.get("error", "")
    if event_type == "rate_limited":
        return (
            f"{payload.get('scope', '')} · {payload.get('count', 0)}/{payload.get('limit', 0)} · "
            f"retry_after={payload.get('retry_after', 0)}s"
        )
    return _summarize_workflow_event(event_type, payload)


def _summarize_workflow_event(event_type: str, payload: dict[str, Any]) -> str:
    """为重构后的工作流事件生成摘要（§二十四）。

    与 ``_summarize_event`` 分开只是为了让两段 if 链都保持可读：这里只覆盖
    Query Rewrite → Fact Analysis → Complexity → Plan → Evidence → Verify →
    Repair → Answer 这条链路上的事件。
    """
    if event_type == "query_rewrite":
        if not payload.get("changed"):
            return f"未改写 · {payload.get('input_chars', 0)} 字"
        return f"{payload.get('input_chars', 0)} → {payload.get('output_chars', 0)} 字"
    if event_type == "fact_analysis":
        return (
            f"事实充分={payload.get('facts_sufficient')} · "
            f"需补问={payload.get('needs_clarification')} · "
            f"事实缺口 {len(payload.get('missing_facts') or [])} 项"
        )
    if event_type == "clarification_required":
        return (
            f"第 {payload.get('clarification_round', 0)}/{payload.get('max_rounds', 0)} 轮 · "
            f"{len(payload.get('questions') or [])} 个问题"
        )
    if event_type == "clarification_resumed":
        return f"第 {payload.get('clarification_round', 0)} 轮回复 · {payload.get('reply_chars', 0)} 字"
    if event_type == "complexity_route":
        return (
            f"{payload.get('complexity_level', '')} · {payload.get('execution_mode', '')} · "
            f"类案检索={payload.get('needs_case_retrieval')}"
        )
    if event_type == "plan_created":
        degraded = " · 已降级" if payload.get("planner_degraded") else ""
        return f"{payload.get('intent', '')} · {payload.get('step_count', 0)} 个步骤{degraded}"
    if event_type in {"planner_degraded", "verification_degraded", "repair_skipped"}:
        return payload.get("reason", "")
    if event_type in {"agent_fallback", "verifier_llm_skipped"}:
        return payload.get("error", "")
    return _summarize_execution_event(event_type, payload)


def _summarize_execution_event(event_type: str, payload: dict[str, Any]) -> str:
    """计划执行、证据归一化、核验与修复阶段的事件摘要。"""
    if event_type == "plan_step_started":
        return f"{payload.get('step_id', '')} → {payload.get('assigned_agent', '')}"
    if event_type == "plan_step_completed":
        return (
            f"{payload.get('step_id', '')} → {payload.get('assigned_agent', '')} · "
            f"{payload.get('report_id', '')}"
        )
    if event_type == "plan_step_retry":
        return (
            f"{payload.get('step_id', '')} → {payload.get('assigned_agent', '')} · "
            f"第 {payload.get('retry_count', 0)} 次重试"
        )
    if event_type == "plan_step_failed":
        reason = payload.get("reason") or ""
        suffix = f" · {reason}" if reason else ""
        return f"{payload.get('step_id', '')} → {payload.get('assigned_agent', '')}{suffix}"
    if event_type == "tool_loop_stopped":
        return f"{payload.get('reason', '')} · 已调用 {payload.get('tool_call_count', 0)} 次"
    if event_type == "evidence_normalized":
        return (
            f"法条 {payload.get('unique_law_count', 0)} 条 / 案例 "
            f"{payload.get('unique_case_count', 0)} 条 · 增益 {payload.get('evidence_gain', 0)} · "
            f"丢弃 {payload.get('dropped_count', 0)}"
        )
    if event_type == "evidence_deduplicated":
        return f"去重丢弃 {payload.get('dropped_count', 0)} 条"
    if event_type == "verification_complete":
        report = payload.get("citation_report") or {}
        return (
            f"passed={payload.get('passed')} · score={payload.get('score')} · "
            f"引用 {report.get('citation_verified', 0)}/{report.get('citation_total', 0)}"
        )
    if event_type == "verification_issue":
        return (
            f"{payload.get('type', '')} · {payload.get('severity', '')} · "
            f"{payload.get('message', '')}"
        )
    if event_type == "repair_started":
        targets = payload.get("targets") or []
        return (
            f"{'、'.join(str(item) for item in targets)} · "
            f"重开 {len(payload.get('reopened_steps') or [])} 步 · "
            f"第 {payload.get('repair_count', 0)} 轮"
        )
    if event_type == "replan_skipped":
        return f"{payload.get('execution_mode', '')} · {payload.get('retry_reason') or ''}"
    if event_type == "answer_citation_rejected":
        return (
            f"第 {payload.get('attempt', 0)} 稿 · "
            f"越界引用 {len(payload.get('ungrounded') or [])} 处"
        )
    if event_type == "context_build":
        return (
            f"{payload.get('context_tier', '-')} 档 · prompt "
            f"{payload.get('estimated_prompt_tokens', 0)}/"
            f"{payload.get('prompt_token_budget', 0)} tokens · 法条 "
            f"{payload.get('selected_law_count', 0)} 条 / 案例 {payload.get('selected_case_count', 0)} 条"
        )
    if event_type in {"context_status", "context_compaction"}:
        return (
            f"{payload.get('message_count', 0)} 条消息 · "
            f"{payload.get('estimated_tokens', 0)}/{payload.get('token_budget', 0)} tokens · "
            f"should_compact={payload.get('should_compact')}"
        )
    if event_type == "viking_context_retrieval":
        return f"命中 {payload.get('total', 0)} 条"
    return ""


def eval_trends(limit: int = 20) -> dict[str, Any]:
    """
    函数作用：
        返回评测历史趋势，按时间正序排列。
    输入参数：
        - limit: int，默认值 20
    输出参数：
        - dict[str, Any]
    """
    runs = list(reversed(list_eval_runs(limit=limit)))
    metric_names = sorted({
        key
        for run in runs
        for key, value in run.get("metrics", {}).items()
        if isinstance(value, (int, float))
    })
    series = {
        name: [
            {"run_id": run["id"], "value": run.get("metrics", {}).get(name), "created_at": run["created_at"]}
            for run in runs
        ]
        for name in metric_names
    }
    return {"runs": runs, "series": series}
