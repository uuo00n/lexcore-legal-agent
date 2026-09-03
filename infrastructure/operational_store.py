"""PostgreSQL 运行数据存储。

上传文档、摘要、用户画像、每日配额与后台可观测性数据统一存入 PostgreSQL。
生产实现只接受 PostgreSQL ``Engine``；``InMemoryOperationalStore`` 仅用于单元测试
显式注入，避免测试重新引入嵌入式数据库。
"""
from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any, Protocol

from sqlalchemy import Engine, inspect, text


REQUIRED_TABLES = frozenset(
    {
        "documents",
        "conversation_summaries",
        "user_profiles",
        "quota_usage",
        "llm_call_logs",
        "agent_traces",
        "agent_events",
        "eval_runs",
    }
)


class OperationalStore(Protocol):
    """运行数据所需的同步存储契约。"""

    def validate_schema(self) -> None: ...
    def save_document(self, row: dict[str, Any]) -> None: ...
    def load_document(self, doc_id: str) -> dict[str, Any] | None: ...
    def save_summary(self, thread_id: str, summary: str, msg_count: int, updated_at: int) -> None: ...
    def get_summary(self, thread_id: str) -> dict[str, Any] | None: ...
    def save_user_profile(self, thread_id: str, profile_json: str, updated_at: int) -> None: ...
    def get_user_profile(self, thread_id: str) -> Any | None: ...
    def get_quota(self, subject: str, usage_date: str) -> dict[str, Any] | None: ...
    def consume_quota(self, subject: str, usage_date: str, request_limit: int, token_limit: int, updated_at: int) -> dict[str, Any]: ...
    def add_token_usage(self, subject: str, usage_date: str, token_count: int, updated_at: int) -> None: ...
    def list_quota(self, limit: int) -> list[dict[str, Any]]: ...
    def create_trace(self, row: dict[str, Any]) -> None: ...
    def complete_trace(self, trace_id: str, values: dict[str, Any]) -> None: ...
    def add_event(self, row: dict[str, Any]) -> None: ...
    def add_llm_call(self, row: dict[str, Any]) -> None: ...
    def add_eval_run(self, row: dict[str, Any]) -> None: ...
    def dashboard_summary(self) -> dict[str, Any]: ...
    def list_traces(self, limit: int) -> list[dict[str, Any]]: ...
    def list_events(self, trace_id: str) -> list[dict[str, Any]]: ...
    def list_llm_calls(self, limit: int) -> list[dict[str, Any]]: ...
    def list_eval_runs(self, limit: int) -> list[dict[str, Any]]: ...


class PostgresOperationalStore:
    """基于 SQLAlchemy 同步 Engine 的 PostgreSQL 实现。"""

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("operational store requires PostgreSQL")
        self._engine = engine

    def validate_schema(self) -> None:
        """只验证迁移结果；应用运行时绝不自行建表。"""
        with self._engine.connect() as connection:
            inspector = inspect(connection)
            missing = sorted(name for name in REQUIRED_TABLES if not inspector.has_table(name))
        if missing:
            raise RuntimeError(
                "PostgreSQL schema is incomplete; run 'alembic upgrade head'. "
                f"Missing tables: {', '.join(missing)}"
            )

    def _execute(self, statement: str, params: dict[str, Any]) -> None:
        with self._engine.begin() as connection:
            connection.execute(text(statement), params)

    def _one(self, statement: str, params: dict[str, Any]) -> dict[str, Any] | None:
        with self._engine.connect() as connection:
            row = connection.execute(text(statement), params).mappings().first()
        return dict(row) if row is not None else None

    def _all(self, statement: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = connection.execute(text(statement), params).mappings().all()
        return [dict(row) for row in rows]

    def save_document(self, row: dict[str, Any]) -> None:
        self._execute(
            """
            INSERT INTO documents
                (doc_id, filename, content, char_count, truncated, created_at)
            VALUES
                (:doc_id, :filename, :content, :char_count, :truncated, :created_at)
            ON CONFLICT (doc_id) DO UPDATE SET
                filename = EXCLUDED.filename,
                content = EXCLUDED.content,
                char_count = EXCLUDED.char_count,
                truncated = EXCLUDED.truncated,
                created_at = EXCLUDED.created_at
            """,
            row,
        )

    def load_document(self, doc_id: str) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT doc_id, filename, content AS text, char_count, truncated
            FROM documents WHERE doc_id = :doc_id
            """,
            {"doc_id": doc_id},
        )

    def save_summary(self, thread_id: str, summary: str, msg_count: int, updated_at: int) -> None:
        self._execute(
            """
            INSERT INTO conversation_summaries (thread_id, summary, msg_count, updated_at)
            VALUES (:thread_id, :summary, :msg_count, :updated_at)
            ON CONFLICT (thread_id) DO UPDATE SET
                summary = EXCLUDED.summary,
                msg_count = EXCLUDED.msg_count,
                updated_at = EXCLUDED.updated_at
            """,
            {
                "thread_id": thread_id,
                "summary": summary,
                "msg_count": msg_count,
                "updated_at": updated_at,
            },
        )

    def get_summary(self, thread_id: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT summary, msg_count FROM conversation_summaries WHERE thread_id = :thread_id",
            {"thread_id": thread_id},
        )

    def save_user_profile(self, thread_id: str, profile_json: str, updated_at: int) -> None:
        self._execute(
            """
            INSERT INTO user_profiles (thread_id, profile, updated_at)
            VALUES (:thread_id, CAST(:profile AS jsonb), :updated_at)
            ON CONFLICT (thread_id) DO UPDATE SET
                profile = EXCLUDED.profile,
                updated_at = EXCLUDED.updated_at
            """,
            {"thread_id": thread_id, "profile": profile_json, "updated_at": updated_at},
        )

    def get_user_profile(self, thread_id: str) -> Any | None:
        row = self._one(
            "SELECT profile FROM user_profiles WHERE thread_id = :thread_id",
            {"thread_id": thread_id},
        )
        return row["profile"] if row is not None else None

    def get_quota(self, subject: str, usage_date: str) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT request_count, token_count
            FROM quota_usage
            WHERE subject = :subject AND usage_date = CAST(:usage_date AS date)
            """,
            {"subject": subject, "usage_date": usage_date},
        )

    def consume_quota(
        self,
        subject: str,
        usage_date: str,
        request_limit: int,
        token_limit: int,
        updated_at: int,
    ) -> dict[str, Any]:
        params = {
            "subject": subject,
            "usage_date": usage_date,
            "request_limit": request_limit,
            "token_limit": token_limit,
            "updated_at": updated_at,
        }
        statement = text(
            """
            INSERT INTO quota_usage
                (subject, usage_date, request_count, token_count, updated_at)
            VALUES (:subject, CAST(:usage_date AS date), 1, 0, :updated_at)
            ON CONFLICT (subject, usage_date) DO UPDATE SET
                request_count = quota_usage.request_count + 1,
                updated_at = EXCLUDED.updated_at
            WHERE (:request_limit = 0 OR quota_usage.request_count < :request_limit)
              AND (:token_limit = 0 OR quota_usage.token_count < :token_limit)
            RETURNING request_count, token_count
            """
        )
        with self._engine.begin() as connection:
            row = connection.execute(statement, params).mappings().first()
            if row is not None:
                return {**dict(row), "consumed": True}
            current = connection.execute(
                text(
                    """
                    SELECT request_count, token_count FROM quota_usage
                    WHERE subject = :subject AND usage_date = CAST(:usage_date AS date)
                    """
                ),
                params,
            ).mappings().one()
            return {**dict(current), "consumed": False}

    def add_token_usage(
        self,
        subject: str,
        usage_date: str,
        token_count: int,
        updated_at: int,
    ) -> None:
        self._execute(
            """
            INSERT INTO quota_usage
                (subject, usage_date, request_count, token_count, updated_at)
            VALUES (:subject, CAST(:usage_date AS date), 0, :token_count, :updated_at)
            ON CONFLICT (subject, usage_date) DO UPDATE SET
                token_count = quota_usage.token_count + EXCLUDED.token_count,
                updated_at = EXCLUDED.updated_at
            """,
            {
                "subject": subject,
                "usage_date": usage_date,
                "token_count": token_count,
                "updated_at": updated_at,
            },
        )

    def list_quota(self, limit: int) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT subject, usage_date::text AS usage_date,
                   request_count, token_count, updated_at
            FROM quota_usage ORDER BY updated_at DESC LIMIT :limit
            """,
            {"limit": limit},
        )

    def create_trace(self, row: dict[str, Any]) -> None:
        self._execute(
            """
            INSERT INTO agent_traces
                (trace_id, thread_id, user_message, status, started_at)
            VALUES (:trace_id, :thread_id, :user_message, 'running', :started_at)
            ON CONFLICT (trace_id) DO UPDATE SET
                thread_id = EXCLUDED.thread_id,
                user_message = EXCLUDED.user_message,
                final_answer = '',
                status = 'running',
                legal_analysis = '{}'::jsonb,
                started_at = EXCLUDED.started_at,
                completed_at = NULL,
                latency_ms = 0,
                error = ''
            """,
            row,
        )

    def complete_trace(self, trace_id: str, values: dict[str, Any]) -> None:
        self._execute(
            """
            UPDATE agent_traces SET
                final_answer = :final_answer,
                status = :status,
                legal_analysis = CAST(:legal_analysis AS jsonb),
                completed_at = :completed_at,
                latency_ms = GREATEST(0, :completed_at - started_at),
                error = :error
            WHERE trace_id = :trace_id
            """,
            {"trace_id": trace_id, **values},
        )

    def add_event(self, row: dict[str, Any]) -> None:
        self._execute(
            """
            INSERT INTO agent_events (trace_id, event_type, name, payload, created_at)
            VALUES (:trace_id, :event_type, :name, CAST(:payload AS jsonb), :created_at)
            """,
            row,
        )

    def add_llm_call(self, row: dict[str, Any]) -> None:
        self._execute(
            """
            INSERT INTO llm_call_logs
                (trace_id, thread_id, provider, model, base_url, status, latency_ms,
                 error, fallback_from, model_route, prompt_tokens, completion_tokens,
                 total_tokens, created_at)
            VALUES
                (:trace_id, :thread_id, :provider, :model, :base_url, :status,
                 :latency_ms, :error, :fallback_from, :model_route, :prompt_tokens,
                 :completion_tokens, :total_tokens, :created_at)
            """,
            row,
        )

    def add_eval_run(self, row: dict[str, Any]) -> None:
        self._execute(
            """
            INSERT INTO eval_runs
                (mode, top_k, num_queries, metrics, result_path, details, created_at)
            VALUES
                (:mode, :top_k, :num_queries, CAST(:metrics AS jsonb), :result_path,
                 CAST(:details AS jsonb), :created_at)
            """,
            row,
        )

    def dashboard_summary(self) -> dict[str, Any]:
        row = self._one(
            """
            SELECT
              (SELECT COUNT(*) FROM agent_traces) AS total_traces,
              (SELECT COUNT(*) FROM agent_traces WHERE status = 'success') AS success_traces,
              (SELECT COALESCE(AVG(latency_ms), 0) FROM agent_traces WHERE latency_ms > 0) AS avg_latency,
              (SELECT COUNT(*) FROM llm_call_logs) AS llm_calls,
              (SELECT COUNT(*) FROM llm_call_logs WHERE status <> 'success') AS failed_llm_calls,
              (SELECT COUNT(*) FROM llm_call_logs WHERE fallback_from <> '') AS fallback_count,
              (SELECT COUNT(*) FROM llm_call_logs WHERE model_route <> '') AS routed_count,
              (SELECT COUNT(*) FROM eval_runs) AS eval_count
            """,
            {},
        )
        assert row is not None
        return row

    def list_traces(self, limit: int) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT trace_id, thread_id, user_message, final_answer, status,
                   legal_analysis, started_at, completed_at, latency_ms, error
            FROM agent_traces ORDER BY started_at DESC LIMIT :limit
            """,
            {"limit": limit},
        )

    def list_events(self, trace_id: str) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT id, event_type, name, payload, created_at
            FROM agent_events WHERE trace_id = :trace_id ORDER BY id ASC
            """,
            {"trace_id": trace_id},
        )

    def list_llm_calls(self, limit: int) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT id, trace_id, thread_id, provider, model, base_url, status,
                   latency_ms, error, fallback_from, model_route, prompt_tokens,
                   completion_tokens, total_tokens, created_at
            FROM llm_call_logs ORDER BY created_at DESC LIMIT :limit
            """,
            {"limit": limit},
        )

    def list_eval_runs(self, limit: int) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT id, mode, top_k, num_queries, metrics, result_path, details, created_at
            FROM eval_runs ORDER BY created_at DESC LIMIT :limit
            """,
            {"limit": limit},
        )


class InMemoryOperationalStore:
    """测试专用的线程安全内存实现；生产启动路径不会选择它。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.documents: dict[str, dict[str, Any]] = {}
        self.summaries: dict[str, dict[str, Any]] = {}
        self.profiles: dict[str, Any] = {}
        self.quotas: dict[tuple[str, str], dict[str, Any]] = {}
        self.traces: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.llm_calls: list[dict[str, Any]] = []
        self.eval_runs: list[dict[str, Any]] = []

    def validate_schema(self) -> None:
        return None

    def save_document(self, row: dict[str, Any]) -> None:
        with self._lock:
            self.documents[row["doc_id"]] = deepcopy(row)

    def load_document(self, doc_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.documents.get(doc_id)
            if row is None:
                return None
            result = deepcopy(row)
            result["text"] = result.pop("content")
            return result

    def save_summary(self, thread_id: str, summary: str, msg_count: int, updated_at: int) -> None:
        with self._lock:
            self.summaries[thread_id] = {
                "summary": summary,
                "msg_count": msg_count,
                "updated_at": updated_at,
            }

    def get_summary(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.summaries.get(thread_id)
            return deepcopy(row) if row is not None else None

    def save_user_profile(self, thread_id: str, profile_json: str, updated_at: int) -> None:
        import json

        with self._lock:
            self.profiles[thread_id] = json.loads(profile_json)

    def get_user_profile(self, thread_id: str) -> Any | None:
        with self._lock:
            return deepcopy(self.profiles.get(thread_id))

    def get_quota(self, subject: str, usage_date: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.quotas.get((subject, usage_date))
            return deepcopy(row) if row is not None else None

    def consume_quota(self, subject: str, usage_date: str, request_limit: int, token_limit: int, updated_at: int) -> dict[str, Any]:
        with self._lock:
            row = self.quotas.setdefault(
                (subject, usage_date),
                {"subject": subject, "usage_date": usage_date, "request_count": 0, "token_count": 0, "updated_at": updated_at},
            )
            allowed = (not request_limit or row["request_count"] < request_limit) and (
                not token_limit or row["token_count"] < token_limit
            )
            if allowed:
                row["request_count"] += 1
                row["updated_at"] = updated_at
            return {"request_count": row["request_count"], "token_count": row["token_count"], "consumed": allowed}

    def add_token_usage(self, subject: str, usage_date: str, token_count: int, updated_at: int) -> None:
        with self._lock:
            row = self.quotas.setdefault(
                (subject, usage_date),
                {"subject": subject, "usage_date": usage_date, "request_count": 0, "token_count": 0, "updated_at": updated_at},
            )
            row["token_count"] += token_count
            row["updated_at"] = updated_at

    def list_quota(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(self.quotas.values(), key=lambda row: row["updated_at"], reverse=True)
            return deepcopy(rows[:limit])

    def create_trace(self, row: dict[str, Any]) -> None:
        with self._lock:
            self.traces[row["trace_id"]] = {
                **deepcopy(row),
                "final_answer": "",
                "status": "running",
                "legal_analysis": {},
                "completed_at": None,
                "latency_ms": 0,
                "error": "",
            }

    def complete_trace(self, trace_id: str, values: dict[str, Any]) -> None:
        import json

        with self._lock:
            row = self.traces.get(trace_id)
            if row is None:
                return
            row.update(deepcopy(values))
            raw_analysis = row.get("legal_analysis")
            if isinstance(raw_analysis, str):
                row["legal_analysis"] = json.loads(raw_analysis)
            row["latency_ms"] = max(0, int(values["completed_at"]) - int(row["started_at"]))

    def add_event(self, row: dict[str, Any]) -> None:
        import json

        with self._lock:
            item = deepcopy(row)
            item["id"] = len(self.events) + 1
            if isinstance(item.get("payload"), str):
                item["payload"] = json.loads(item["payload"])
            self.events.append(item)

    def add_llm_call(self, row: dict[str, Any]) -> None:
        with self._lock:
            self.llm_calls.append({"id": len(self.llm_calls) + 1, **deepcopy(row)})

    def add_eval_run(self, row: dict[str, Any]) -> None:
        import json

        with self._lock:
            item = {"id": len(self.eval_runs) + 1, **deepcopy(row)}
            for key in ("metrics", "details"):
                if isinstance(item.get(key), str):
                    item[key] = json.loads(item[key])
            self.eval_runs.append(item)

    def dashboard_summary(self) -> dict[str, Any]:
        with self._lock:
            latencies = [row["latency_ms"] for row in self.traces.values() if row["latency_ms"] > 0]
            return {
                "total_traces": len(self.traces),
                "success_traces": sum(row["status"] == "success" for row in self.traces.values()),
                "avg_latency": sum(latencies) / len(latencies) if latencies else 0,
                "llm_calls": len(self.llm_calls),
                "failed_llm_calls": sum(row["status"] != "success" for row in self.llm_calls),
                "fallback_count": sum(bool(row.get("fallback_from")) for row in self.llm_calls),
                "routed_count": sum(bool(row.get("model_route")) for row in self.llm_calls),
                "eval_count": len(self.eval_runs),
            }

    def list_traces(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(self.traces.values(), key=lambda row: row["started_at"], reverse=True)
            return deepcopy(rows[:limit])

    def list_events(self, trace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy([row for row in self.events if row["trace_id"] == trace_id])

    def list_llm_calls(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(self.llm_calls, key=lambda row: row["created_at"], reverse=True)
            return deepcopy(rows[:limit])

    def list_eval_runs(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(self.eval_runs, key=lambda row: row["created_at"], reverse=True)
            return deepcopy(rows[:limit])


_store: OperationalStore | None = None


def init_operational_store(store: OperationalStore | None = None) -> OperationalStore:
    """初始化运行数据存储；生产默认绑定 PostgreSQL 同步连接池。"""
    global _store
    if store is not None:
        _store = store
    elif _store is None:
        from infrastructure.database import get_sync_engine

        _store = PostgresOperationalStore(get_sync_engine())
    _store.validate_schema()
    return _store


def get_operational_store() -> OperationalStore:
    if _store is None:
        raise RuntimeError("operational store not initialized; call init_operational_store() first")
    return _store


def reset_operational_store() -> None:
    global _store
    _store = None


__all__ = [
    "InMemoryOperationalStore",
    "OperationalStore",
    "PostgresOperationalStore",
    "REQUIRED_TABLES",
    "get_operational_store",
    "init_operational_store",
    "reset_operational_store",
]
