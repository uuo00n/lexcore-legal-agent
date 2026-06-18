from __future__ import annotations

from services.checkpoint import init_meta_db, reset_for_tests
from services.observability import init_observability_tables, list_eval_runs


def setup_function():
    reset_for_tests()


def teardown_function():
    reset_for_tests()


def test_record_eval_history_from_results(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DB", str(tmp_path / "meta.sqlite"))
    init_meta_db()
    init_observability_tables()

    from services.observability import record_eval_run

    record_eval_run(
        {
            "mode": "retrieval",
            "top_k": 5,
            "num_queries": 3,
            "aggregated": {"hit_rate": 0.66, "mrr": 0.5},
            "details": [{"question": "测试"}],
        },
        str(tmp_path / "result.json"),
    )

    runs = list_eval_runs()
    assert runs[0]["mode"] == "retrieval"
    assert runs[0]["top_k"] == 5
    assert runs[0]["metrics"]["hit_rate"] == 0.66
