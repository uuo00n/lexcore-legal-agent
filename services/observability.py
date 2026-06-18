"""Agent 可观测性存储层。

本模块把 LLM 调用、Agent 运行轨迹、工具事件和评测结果统一写入
现有的元数据库。它只负责结构化记录和查询，不依赖 FastAPI 或 LangGraph，
方便在 API、Agent 节点和评测脚本中复用。
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from services.checkpoint import get_meta_conn


def _json_dumps(value: Any) -> str:
    """
    函数作用：
        将任意可 JSON 序列化对象转为数据库中的文本格式。
    输入参数：
        - value: Any
    输出参数：
        - str
    """
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str | None, default: Any) -> Any:
    """
    函数作用：
        从数据库文本字段恢复 JSON，失败时返回默认值。
    输入参数：
        - value: str | None
        - default: Any
    输出参数：
        - Any
    """
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def init_observability_tables() -> None:
    """
    函数作用：
        初始化可观测性相关 SQLite 表，支持重复调用。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    conn = get_meta_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS llm_call_logs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id         TEXT,
            thread_id        TEXT,
            provider         TEXT NOT NULL,
            model            TEXT NOT NULL,
            base_url         TEXT NOT NULL DEFAULT '',
            status           TEXT NOT NULL,
            latency_ms       INTEGER NOT NULL DEFAULT 0,
            error            TEXT NOT NULL DEFAULT '',
            fallback_from    TEXT NOT NULL DEFAULT '',
            model_route      TEXT NOT NULL DEFAULT '',
            prompt_tokens    INTEGER,
            completion_tokens INTEGER,
            total_tokens     INTEGER,
            created_at       INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_llm_call_logs_trace
            ON llm_call_logs(trace_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_llm_call_logs_created
            ON llm_call_logs(created_at DESC);

        CREATE TABLE IF NOT EXISTS agent_traces (
            trace_id          TEXT PRIMARY KEY,
            thread_id         TEXT NOT NULL,
            user_message      TEXT NOT NULL,
            final_answer      TEXT NOT NULL DEFAULT '',
            status            TEXT NOT NULL DEFAULT 'running',
            legal_analysis    TEXT NOT NULL DEFAULT '{}',
            started_at        INTEGER NOT NULL,
            completed_at      INTEGER,
            latency_ms        INTEGER NOT NULL DEFAULT 0,
            error             TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_agent_traces_thread
            ON agent_traces(thread_id, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_agent_traces_started
            ON agent_traces(started_at DESC);

        CREATE TABLE IF NOT EXISTS agent_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id    TEXT NOT NULL,
            event_type  TEXT NOT NULL,
            name        TEXT NOT NULL DEFAULT '',
            payload     TEXT NOT NULL DEFAULT '{}',
            created_at  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_events_trace
            ON agent_events(trace_id, id);

        CREATE TABLE IF NOT EXISTS eval_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            mode        TEXT NOT NULL,
            top_k       INTEGER,
            num_queries INTEGER NOT NULL DEFAULT 0,
            metrics     TEXT NOT NULL DEFAULT '{}',
            result_path TEXT NOT NULL DEFAULT '',
            details     TEXT NOT NULL DEFAULT '[]',
            created_at  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_eval_runs_created
            ON eval_runs(created_at DESC);
        """
    )
    _ensure_column("llm_call_logs", "model_route", "TEXT NOT NULL DEFAULT ''")
    conn.commit()


def _ensure_column(table: str, column: str, definition: str) -> None:
    """
    函数作用：
        为已有 SQLite 表补充新列，支持渐进式升级。
    输入参数：
        - table: str
        - column: str
        - definition: str
    输出参数：
        - 无
    """
    conn = get_meta_conn()
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
    conn = get_meta_conn()
    now = int(time.time() * 1000)
    conn.execute(
        """INSERT OR REPLACE INTO agent_traces
           (trace_id, thread_id, user_message, status, started_at)
           VALUES (?, ?, ?, 'running', ?)""",
        (trace_id, thread_id, user_message, now),
    )
    conn.commit()


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
    conn = get_meta_conn()
    now = int(time.time() * 1000)
    cur = conn.execute("SELECT started_at FROM agent_traces WHERE trace_id = ?", (trace_id,))
    row = cur.fetchone()
    started_at = row[0] if row else now
    conn.execute(
        """UPDATE agent_traces
           SET final_answer = ?, status = ?, legal_analysis = ?, completed_at = ?,
               latency_ms = ?, error = ?
           WHERE trace_id = ?""",
        (
            final_answer,
            status,
            _json_dumps(legal_analysis or {}),
            now,
            max(0, now - started_at),
            error,
            trace_id,
        ),
    )
    conn.commit()


def record_event(
    trace_id: str,
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
    if not trace_id:
        return
    conn = get_meta_conn()
    conn.execute(
        """INSERT INTO agent_events (trace_id, event_type, name, payload, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (trace_id, event_type, name, _json_dumps(payload or {}), int(time.time() * 1000)),
    )
    conn.commit()


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
    conn = get_meta_conn()
    conn.execute(
        """INSERT INTO llm_call_logs
           (trace_id, thread_id, provider, model, base_url, status, latency_ms, error,
            fallback_from, model_route, prompt_tokens, completion_tokens, total_tokens, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            trace_id,
            thread_id,
            provider,
            model,
            base_url,
            status,
            latency_ms,
            error,
            fallback_from,
            model_route,
            usage.get("prompt_tokens") or usage.get("input_tokens"),
            usage.get("completion_tokens") or usage.get("output_tokens"),
            usage.get("total_tokens"),
            int(time.time() * 1000),
        ),
    )
    conn.commit()


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
    conn = get_meta_conn()
    conn.execute(
        """INSERT INTO eval_runs
           (mode, top_k, num_queries, metrics, result_path, details, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            results.get("mode", "unknown"),
            results.get("top_k"),
            results.get("num_queries", 0),
            _json_dumps(results.get("aggregated", {})),
            result_path,
            _json_dumps(results.get("details", [])),
            int(time.time() * 1000),
        ),
    )
    conn.commit()


def dashboard_summary() -> dict[str, Any]:
    """
    函数作用：
        汇总后台看板顶部指标。
    输入参数：
        - 无
    输出参数：
        - dict[str, Any]
    """
    conn = get_meta_conn()
    total_traces = conn.execute("SELECT COUNT(*) FROM agent_traces").fetchone()[0]
    success_traces = conn.execute(
        "SELECT COUNT(*) FROM agent_traces WHERE status = 'success'"
    ).fetchone()[0]
    avg_latency = conn.execute(
        "SELECT COALESCE(AVG(latency_ms), 0) FROM agent_traces WHERE latency_ms > 0"
    ).fetchone()[0]
    llm_calls = conn.execute("SELECT COUNT(*) FROM llm_call_logs").fetchone()[0]
    failed_llm_calls = conn.execute(
        "SELECT COUNT(*) FROM llm_call_logs WHERE status != 'success'"
    ).fetchone()[0]
    fallback_count = conn.execute(
        "SELECT COUNT(*) FROM llm_call_logs WHERE fallback_from != ''"
    ).fetchone()[0]
    routed_count = conn.execute(
        "SELECT COUNT(*) FROM llm_call_logs WHERE model_route != ''"
    ).fetchone()[0]
    eval_count = conn.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0]
    return {
        "total_traces": total_traces,
        "success_rate": (success_traces / total_traces) if total_traces else 0.0,
        "avg_trace_latency_ms": int(avg_latency or 0),
        "llm_calls": llm_calls,
        "failed_llm_calls": failed_llm_calls,
        "fallback_count": fallback_count,
        "routed_count": routed_count,
        "eval_runs": eval_count,
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
    conn = get_meta_conn()
    cur = conn.execute(
        """SELECT trace_id, thread_id, user_message, final_answer, status,
                  legal_analysis, started_at, completed_at, latency_ms, error
           FROM agent_traces
           ORDER BY started_at DESC
           LIMIT ?""",
        (limit,),
    )
    return [
        {
            "trace_id": row[0],
            "thread_id": row[1],
            "user_message": row[2],
            "final_answer": row[3],
            "status": row[4],
            "legal_analysis": _json_loads(row[5], {}),
            "started_at": row[6],
            "completed_at": row[7],
            "latency_ms": row[8],
            "error": row[9],
        }
        for row in cur.fetchall()
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
    conn = get_meta_conn()
    cur = conn.execute(
        """SELECT id, event_type, name, payload, created_at
           FROM agent_events
           WHERE trace_id = ?
           ORDER BY id ASC""",
        (trace_id,),
    )
    trace = traces[0]
    trace["events"] = [
        {
            "id": row[0],
            "event_type": row[1],
            "name": row[2],
            "payload": _json_loads(row[3], {}),
            "created_at": row[4],
        }
        for row in cur.fetchall()
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
    conn = get_meta_conn()
    cur = conn.execute(
        """SELECT id, trace_id, thread_id, provider, model, base_url, status,
                  latency_ms, error, fallback_from, model_route, prompt_tokens,
                  completion_tokens, total_tokens, created_at
           FROM llm_call_logs
           ORDER BY created_at DESC
           LIMIT ?""",
        (limit,),
    )
    return [
        {
            "id": row[0],
            "trace_id": row[1],
            "thread_id": row[2],
            "provider": row[3],
            "model": row[4],
            "base_url": row[5],
            "status": row[6],
            "latency_ms": row[7],
            "error": row[8],
            "fallback_from": row[9],
            "model_route": row[10],
            "prompt_tokens": row[11],
            "completion_tokens": row[12],
            "total_tokens": row[13],
            "created_at": row[14],
        }
        for row in cur.fetchall()
    ]


def list_eval_runs(limit: int = 20) -> list[dict[str, Any]]:
    """
    函数作用：
        查询最近评测历史。
    输入参数：
        - limit: int，默认值 20
    输出参数：
        - list[dict[str, Any]]
    """
    conn = get_meta_conn()
    cur = conn.execute(
        """SELECT id, mode, top_k, num_queries, metrics, result_path, details, created_at
           FROM eval_runs
           ORDER BY created_at DESC
           LIMIT ?""",
        (limit,),
    )
    return [
        {
            "id": row[0],
            "mode": row[1],
            "top_k": row[2],
            "num_queries": row[3],
            "metrics": _json_loads(row[4], {}),
            "result_path": row[5],
            "details": _json_loads(row[6], []),
            "created_at": row[7],
        }
        for row in cur.fetchall()
    ]


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
    labels = {
        "chat_start": "用户提交问题",
        "supervisor_route": "中控智能体路由",
        "fact_check": "事实完整性检查",
        "contract_agent": "合同审查智能体",
        "graph_node": "LangGraph 节点执行",
        "model_route": "模型路由决策",
        "case_retrieval": "相似法律场景检索",
        "agent_tool_request": "Agent 请求工具",
        "agent_report": "专家智能体报告",
        "tool_start": "工具调用开始",
        "tool_end": "工具调用完成",
        "retrieval_collect": "法条检索收集",
        "citation_guard": "法条引用校验",
        "llm_error": "LLM 调用失败",
        "llm_fallback": "LLM Fallback",
        "final_answer": "生成最终回答",
        "chat_done": "请求完成",
    }
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
    if event_type == "citation_guard":
        return "回答引用已调整" if payload.get("changed") else "回答引用通过"
    if event_type == "final_answer":
        return payload.get("content_preview", "")
    if event_type == "chat_done":
        return f"{payload.get('elapsed_ms', 0)} ms"
    if event_type == "llm_fallback":
        return f"{payload.get('from')} -> {payload.get('to')}"
    if event_type == "llm_error":
        return payload.get("error", "")
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
