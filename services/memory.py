"""记忆辅助存储层 —— PostgreSQL 摘要与用户画像。

存储职责：
- conversation_summaries: 中期记忆的滚动摘要（滑动窗口溢出部分的压缩）
- user_profiles: 实体记忆（用户画像）

原始消息归档由 PostgreSQL ``messages`` 表保存；长期语义记忆由 Qdrant 保存。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from infrastructure.operational_store import get_operational_store

log = logging.getLogger(__name__)

# ─── 滑动窗口配置 ──────────────────────────────────────────────────────────
# 这两个常量决定「保留多少条近期消息不做摘要」，即摘要与压缩的边界：
# context_compaction 用作 keep_recent，memory_extractor 用作溢出切分点。
# 注意它们不是模型输入窗口——真正注入模型的近期消息条数由
# services/context_builder.py 的 CONTEXT_RECENT_MESSAGE_COUNT（默认 12）控制。
SLIDING_WINDOW_SIZE = 8   # 摘要/压缩时保留的最近消息条数
MAX_WINDOW_TOKENS = 3000  # 滑动窗口 token 上限（超过则提前触发摘要）
CHARS_PER_TOKEN = 1.5     # 中文场景下字符/token 近似比（中文1字≈1.5token，取保守值）


def estimate_tokens(text: str) -> int:
    """
    函数作用：
        粗略估算文本 token 数（中文场景：字符数 / CHARS_PER_TOKEN）。
    输入参数：
        - text: str
    输出参数：
        - int
    """
    return int(len(text) / CHARS_PER_TOKEN)


# ─── 历史摘要（短期记忆的压缩部分） ──────────────────────────────────────────
def save_summary(thread_id: str, summary: str, msg_count: int) -> None:
    """
    函数作用：
        保存/更新对话的历史摘要。
    输入参数：
        - thread_id: str
        - summary: str
        - msg_count: int
    输出参数：
        - 无
    """
    get_operational_store().save_summary(
        thread_id,
        summary,
        msg_count,
        int(time.time()),
    )


def get_summary(thread_id: str) -> Optional[str]:
    """
    函数作用：
        获取对话的历史摘要，不存在则返回 None。
    输入参数：
        - thread_id: str
    输出参数：
        - Optional[str]
    """
    row = get_operational_store().get_summary(thread_id)
    return str(row["summary"]) if row else None


def get_summary_msg_count(thread_id: str) -> int:
    """
    函数作用：
        获取摘要已覆盖的消息数量。
    输入参数：
        - thread_id: str
    输出参数：
        - int
    """
    row = get_operational_store().get_summary(thread_id)
    return int(row["msg_count"]) if row else 0


# ─── 用户画像（实体记忆） ──────────────────────────────────────────────────
def save_user_profile(thread_id: str, profile: dict) -> None:
    """
    函数作用：
        保存/更新用户画像。
    输入参数：
        - thread_id: str
        - profile: dict
    输出参数：
        - 无
    """
    get_operational_store().save_user_profile(
        thread_id,
        json.dumps(profile, ensure_ascii=False),
        int(time.time()),
    )


def get_user_profile(thread_id: str) -> Optional[dict]:
    """
    函数作用：
        获取用户画像，不存在则返回 None。
    输入参数：
        - thread_id: str
    输出参数：
        - Optional[dict]
    """
    profile = get_operational_store().get_user_profile(thread_id)
    if profile is None:
        return None
    if isinstance(profile, dict):
        return profile
    try:
        return json.loads(profile)
    except (json.JSONDecodeError, TypeError):
        return None
