from __future__ import annotations

from services.checkpoint import init_meta_db, reset_for_tests
from services.quota import add_token_usage, consume_request, get_quota_status, init_quota_tables


def setup_function():
    reset_for_tests()


def teardown_function():
    reset_for_tests()


def test_quota_consumes_request_and_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DB", str(tmp_path / "meta.sqlite"))
    monkeypatch.setenv("LEGAL_DAILY_REQUEST_LIMIT", "2")
    monkeypatch.setenv("LEGAL_DAILY_TOKEN_LIMIT", "10")
    init_meta_db()
    init_quota_tables()

    first = consume_request("thread-1")
    second = consume_request("thread-1")
    third = consume_request("thread-1")
    add_token_usage("thread-2", 12)
    token_status = get_quota_status("thread-2")

    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.reason == "daily request quota exceeded"
    assert token_status.allowed is False
    assert token_status.reason == "daily token quota exceeded"
