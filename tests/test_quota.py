from __future__ import annotations

from infrastructure.operational_store import InMemoryOperationalStore, init_operational_store
from services.checkpoint import reset_for_tests
from services.quota import add_token_usage, consume_request, get_quota_status


def setup_function():
    reset_for_tests()
    init_operational_store(InMemoryOperationalStore())


def teardown_function():
    reset_for_tests()


def test_quota_consumes_request_and_tokens(monkeypatch):
    monkeypatch.setenv("LEGAL_DAILY_REQUEST_LIMIT", "2")
    monkeypatch.setenv("LEGAL_DAILY_TOKEN_LIMIT", "10")

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
