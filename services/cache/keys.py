"""缓存 key 构造与内容指纹。

安全约束（本模块是唯一入口）：
- key 中不得出现原始用户提问、合同正文、法条正文、会话标题或任何凭据。
- 所有可变内容一律经 `digest()` / `fingerprint()` 转成 sha256 摘要后再进 key。
- thread_id 属于会话标识（`infrastructure/sanitize.py` 已将 session_id 列为敏感键），
  因此限流与会话元数据同样使用摘要作为分区键；运维可用同样的算法反查特定 subject。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# 各类缓存的命名空间；实际 key 由 infrastructure.redis.make_key 追加全局前缀。
NAMESPACE_RETRIEVAL = "cache:retrieval"
NAMESPACE_DELILEGAL = "cache:delilegal"
NAMESPACE_RATE_LIMIT = "ratelimit"
NAMESPACE_SESSION = "session"
NAMESPACE_IDEMPOTENCY = "idempotency"

# 摘要保留长度：128 bit 足以避免碰撞，同时让 key 保持可读。
DIGEST_LENGTH = 32


def digest(*parts: Any) -> str:
    """
    函数作用：
        把任意数量的片段拼接后取 sha256 摘要，作为 key 中的内容占位。
        分隔符使用 \\x1f，避免不同片段组合产生相同拼接串。
    输入参数：
        - parts: Any，可变参数
    输出参数：
        - str，十六进制摘要前 DIGEST_LENGTH 位
    """
    raw = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def fingerprint(value: Any) -> str:
    """
    函数作用：
        为结构化请求体生成稳定指纹：先做键排序的紧凑 JSON 序列化再取摘要，
        保证同一请求在不同进程/不同字典插入顺序下得到同一 key。
    输入参数：
        - value: Any，JSON 兼容结构
    输出参数：
        - str
    """
    try:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        canonical = repr(value)
    return digest(canonical)


def normalize_text(value: str) -> str:
    """
    函数作用：
        归一化自由文本（压缩空白、去首尾空格），让等价提问命中同一缓存条目。
        返回值只用于喂给 digest()，不会自身进入 key。
    输入参数：
        - value: str
    输出参数：
        - str
    """
    return " ".join(value.strip().split())


__all__ = [
    "DIGEST_LENGTH",
    "NAMESPACE_DELILEGAL",
    "NAMESPACE_IDEMPOTENCY",
    "NAMESPACE_RATE_LIMIT",
    "NAMESPACE_RETRIEVAL",
    "NAMESPACE_SESSION",
    "digest",
    "fingerprint",
    "normalize_text",
]
