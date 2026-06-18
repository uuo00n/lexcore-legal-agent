from __future__ import annotations

import pytest

from services.checkpoint import init_meta_db, reset_for_tests
from services.observability import create_trace, init_observability_tables, record_event, record_eval_run
from services.quota import consume_request, init_quota_tables


def setup_function():
    reset_for_tests()


def teardown_function():
    reset_for_tests()


@pytest.mark.asyncio
async def test_admin_api_returns_summary_and_traces(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DB", str(tmp_path / "meta.sqlite"))
    init_meta_db()
    init_observability_tables()
    init_quota_tables()
    create_trace("trace-1", "thread-1", "劳动合同纠纷")
    record_event("trace-1", "model_route", name="fast", payload={"route": "fast"})
    record_eval_run({"mode": "retrieval", "top_k": 3, "num_queries": 1, "aggregated": {"hit_rate": 1.0}, "details": []})
    consume_request("thread-1")

    from api.admin import admin_eval_trends, admin_quota, admin_summary, admin_trace_timeline, admin_traces

    summary = await admin_summary()
    traces = await admin_traces(limit=30)
    timeline = await admin_trace_timeline("trace-1")
    trends = await admin_eval_trends(limit=20)
    quota = await admin_quota(limit=30)

    assert summary["total_traces"] == 1
    assert traces["items"][0]["trace_id"] == "trace-1"
    assert timeline["timeline"][0]["type"] == "model_route"
    assert trends["series"]["hit_rate"][0]["value"] == 1.0
    assert quota["items"][0]["subject"] == "thread-1"
