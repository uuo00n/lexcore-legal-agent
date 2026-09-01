from __future__ import annotations

from services.checkpoint import init_meta_db, reset_for_tests
from services.observability import (
    complete_trace,
    create_trace,
    dashboard_summary,
    get_trace,
    init_observability_tables,
    list_eval_runs,
    list_llm_calls,
    list_traces,
    record_eval_run,
    record_event,
    record_llm_call,
    trace_context,
)


def setup_function():
    reset_for_tests()


def teardown_function():
    reset_for_tests()


def test_observability_records_trace_llm_and_eval(tmp_path, monkeypatch):
    db_path = tmp_path / "meta.sqlite"
    monkeypatch.setenv("DOCS_DB", str(db_path))
    init_meta_db()
    init_observability_tables()

    create_trace("trace-1", "thread-1", "我的工资被拖欠怎么办")
    record_event("trace-1", "tool_start", name="legal_search", payload={"query": "工资"})
    record_llm_call(
        provider="deepseek",
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
        status="success",
        latency_ms=123,
        trace_id="trace-1",
        thread_id="thread-1",
        usage={"total_tokens": 88},
    )
    complete_trace("trace-1", final_answer="可以先申请劳动仲裁。")
    record_eval_run(
        {"mode": "retrieval", "top_k": 3, "num_queries": 2, "aggregated": {"mrr": 1.0}, "details": []},
        "/tmp/result.json",
    )

    summary = dashboard_summary()
    assert summary["total_traces"] == 1
    assert summary["llm_calls"] == 1
    assert summary["eval_runs"] == 1

    trace = get_trace("trace-1")
    assert trace is not None
    assert trace["events"][0]["event_type"] == "tool_start"
    assert list_traces()[0]["trace_id"] == "trace-1"
    assert list_llm_calls()[0]["total_tokens"] == 88
    assert list_eval_runs()[0]["metrics"]["mrr"] == 1.0


def test_legacy_observability_mirror_redacts_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DB", str(tmp_path / "meta.sqlite"))
    init_meta_db()
    init_observability_tables()

    create_trace("trace-secret", "thread-1", "api_key=abcdef123456")
    record_event(
        "trace-secret",
        "tool_start",
        payload={"headers": {"Authorization": "Bearer abcdef1234567890"}},
    )
    complete_trace(
        "trace-secret",
        error="postgresql://legal:s3cret@db:5432/legal failed",
    )

    trace = get_trace("trace-secret")
    serialized = repr(trace)
    assert "abcdef123456" not in serialized
    assert "s3cret" not in serialized
    assert "***REDACTED***" in serialized


def test_event_schema_is_enriched_from_unified_trace_context(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DB", str(tmp_path / "meta.sqlite"))
    init_meta_db()
    init_observability_tables()
    create_trace("trace-context", "thread-context", "测试统一 trace")

    with trace_context(
        trace_id="trace-context",
        thread_id="thread-context",
        node_name="statute_retrieval_tools",
        agent_name="statute_retrieval_agent",
        tool_name="retrieve_local_law_tool",
        retry_count=2,
    ):
        record_event(
            None,
            "rag_retrieval",
            name="hybrid_retrieval",
            payload={
                "latency_ms": 18,
                "result_count": 4,
                "cache_hit": True,
                "api_key": "sk-1234567890abcdef",
            },
        )

    event = get_trace("trace-context")["events"][0]
    payload = event["payload"]
    assert payload["thread_id"] == "thread-context"
    assert payload["node_name"] == "statute_retrieval_tools"
    assert payload["agent_name"] == "statute_retrieval_agent"
    assert payload["tool_name"] == "retrieve_local_law_tool"
    assert payload["latency_ms"] == 18
    assert payload["token_usage"] == {}
    assert payload["success"] is True
    assert payload["error"] == ""
    assert payload["retrieval_count"] == 4
    assert payload["retry_count"] == 2
    assert payload["cache_hit"] is True
    assert payload["api_key"] == "***REDACTED***"
