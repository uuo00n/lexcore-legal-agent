"""脱敏工具单测 —— 对应要求「API Key 不能写数据库日志」。"""
from __future__ import annotations

import logging

from infrastructure.sanitize import (
    REDACTED,
    RedactingFormatter,
    is_sensitive_key,
    mask_dsn,
    redact,
    redact_mapping,
    redact_text,
)


def test_sensitive_keys_cover_project_credentials():
    """项目实际使用的凭据字段名都应命中敏感键。"""
    for key in (
        "api_key",
        "apiKey",
        "DEEPSEEK_API_KEY",
        "delilegal_secret",
        "app_id",
        "appId",
        "Authorization",
        "admin_key",
        "cookie",
        "password",
        "DATABASE_URL_DSN",
    ):
        assert is_sensitive_key(key), key
    for key in (
        "query",
        "top_k",
        "tool_name",
        "result_count",
        "latency_ms",
        "token_usage",
        "total_tokens",
    ):
        assert not is_sensitive_key(key), key


def test_redact_replaces_whole_value_for_sensitive_key():
    """敏感键的值整体替换，不留前缀后缀。"""
    cleaned = redact({"api_key": "sk-abcdef123456", "query": "劳动合同解除"})
    assert cleaned["api_key"] == REDACTED
    assert cleaned["query"] == "劳动合同解除"


def test_redact_text_catches_inline_secrets():
    """键名无害但值是凭据时也要脱敏。"""
    assert "sk-" not in redact_text("使用 sk-1234567890abcdef 调用失败")
    assert REDACTED in redact_text("Authorization: Bearer abcdef1234567890")
    assert REDACTED in redact_text("app_id=1234567890")
    assert REDACTED in redact_text("连接 postgresql://legal:s3cret@db:5432/legal 超时")


def test_redact_is_recursive_over_nested_structures():
    """嵌套字典与列表都要递归处理。"""
    payload = {
        "tool": "search_law_tool",
        "http": {"headers": {"Authorization": "Bearer abcdef1234567890"}},
        "items": [{"secret": "xyz"}, {"note": "token=abcdef123456"}],
    }
    cleaned = redact(payload)
    assert cleaned["http"]["headers"]["Authorization"] == REDACTED
    assert cleaned["items"][0]["secret"] == REDACTED
    assert REDACTED in cleaned["items"][1]["note"]
    assert cleaned["tool"] == "search_law_tool"


def test_redact_mapping_normalizes_non_dict_input():
    """非字典输入统一包装，None 返回空字典。"""
    assert redact_mapping(None) == {}
    assert redact_mapping(["sk-1234567890abcdef"]) == {"value": [REDACTED]}
    assert redact_mapping({"count": 3}) == {"count": 3}


def test_redact_stops_at_max_depth():
    """超深嵌套不会递归爆栈，末端直接替换为占位。"""
    node: dict[str, object] = {"leaf": "ok"}
    for _ in range(30):
        node = {"child": node}
    cleaned = redact(node)
    flat = repr(cleaned)
    assert REDACTED in flat


def test_mask_dsn_hides_password_only():
    """脱敏后的 DSN 仍可用于定位主机与库名。"""
    masked = mask_dsn("postgresql+asyncpg://legal:s3cret@db.internal:5432/legal")
    assert "s3cret" not in masked
    assert "db.internal:5432/legal" in masked
    assert mask_dsn("sqlite+aiosqlite:///:memory:") == "sqlite+aiosqlite:///:memory:"


def test_logging_formatter_redacts_inline_secret():
    formatter = RedactingFormatter("%(message)s")
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "request failed with Authorization: Bearer abcdef1234567890",
        (),
        None,
    )

    rendered = formatter.format(record)

    assert "abcdef1234567890" not in rendered
    assert REDACTED in rendered
