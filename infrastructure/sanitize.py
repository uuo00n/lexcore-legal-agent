"""结构化 Trace 落库前的敏感信息脱敏。

数据库中的 JSONB Trace 字段来自工具入参、模型配置、HTTP 上下文和错误堆栈，
这些位置都可能夹带 API Key、Secret、Authorization 头或带密码的连接串。
本模块是唯一的脱敏入口：所有仓储在写入 JSONB / error / output_summary 之前
必须调用 `redact()`，因此凭据不会进入数据库日志。
"""
from __future__ import annotations

import logging
import re
from typing import Any

# 脱敏后的占位值，便于在 Trace 中区分“没有该字段”和“字段已脱敏”。
REDACTED = "***REDACTED***"

# 递归深度上限，避免异常嵌套结构导致栈溢出。
MAX_DEPTH = 12

# 键名命中即整体替换；兼容旧字段 app_id，防止历史载荷中的得理凭据落库。
_SENSITIVE_KEY_RE = re.compile(
    r"api[_-]?key|apikey|secret|token|password|passwd|credential"
    r"|authorization|auth[_-]?header|bearer|cookie"
    r"|access[_-]?key|private[_-]?key|app[_-]?id|appid"
    r"|admin[_-]?key|session[_-]?id|dsn|connection[_-]?string",
    re.IGNORECASE,
)

# Token 计数是可观测性指标，不是认证凭据；这些精确键名必须允许落库。
_NON_SECRET_TELEMETRY_KEYS = {
    "token_usage",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
}

# 值内嵌片段命中即局部替换，覆盖 key 名无害但值本身是凭据的场景。
_SECRET_VALUE_RE = re.compile(
    r"sk-[A-Za-z0-9_\-]{8,}"
    r"|tvly-[A-Za-z0-9_\-]{8,}"
    r"|Bearer\s+[A-Za-z0-9._\-]{8,}"
    r"|(?:api[_-]?key|apikey|secret|token|password|app[_-]?id)"
    r"\s*[=:]\s*[\"']?[^\s,&;\"'}]{6,}"
    r"|[A-Za-z][A-Za-z0-9+.\-]*://[^\s:@/]+:[^\s@/]+@",
    re.IGNORECASE,
)

# 连接串密码段，用于日志中安全展示 DSN。
# 用户名段允许为空，覆盖 Redis 常见的 redis://:password@host 写法。
_DSN_PASSWORD_RE = re.compile(r"(?P<head>://[^\s:@/]*:)(?P<password>[^\s@/]+)(?P<tail>@)")


def redact_text(value: str) -> str:
    """
    函数作用：
        对单个字符串做内嵌凭据脱敏。
    输入参数：
        - value: str
    输出参数：
        - str
    """
    return _SECRET_VALUE_RE.sub(REDACTED, value)


def is_sensitive_key(key: str) -> bool:
    """
    函数作用：
        判断字段名是否属于敏感键，命中则整值脱敏。
    输入参数：
        - key: str
    输出参数：
        - bool
    """
    if key.lower() in _NON_SECRET_TELEMETRY_KEYS:
        return False
    return bool(_SENSITIVE_KEY_RE.search(key))


def redact(value: Any, *, _depth: int = 0) -> Any:
    """
    函数作用：
        递归脱敏任意 JSON 兼容结构，返回可安全写入数据库的新对象。
    输入参数：
        - value: Any
        - _depth: int，默认值 0，内部递归深度计数
    输出参数：
        - Any
    """
    if _depth >= MAX_DEPTH:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and is_sensitive_key(key):
                result[key] = REDACTED
            else:
                result[key] = redact(item, _depth=_depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth=_depth + 1) for item in value]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return redact_text(str(value))


def redact_mapping(value: Any) -> dict[str, Any]:
    """
    函数作用：
        脱敏并规范化为 JSONB 字典；非字典输入包装为 {"value": ...}。
    输入参数：
        - value: Any
    输出参数：
        - dict[str, Any]
    """
    if value is None:
        return {}
    cleaned = redact(value)
    if isinstance(cleaned, dict):
        return {str(key): item for key, item in cleaned.items()}
    return {"value": cleaned}


def mask_dsn(dsn: str) -> str:
    """
    函数作用：
        隐藏数据库连接串中的密码，供日志和健康检查安全展示。
    输入参数：
        - dsn: str
    输出参数：
        - str
    """
    return _DSN_PASSWORD_RE.sub(rf"\g<head>{REDACTED}\g<tail>", dsn)


class RedactingFormatter(logging.Formatter):
    """在普通日志最终输出前统一移除 Token、Secret 与带密码连接串。"""

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))
