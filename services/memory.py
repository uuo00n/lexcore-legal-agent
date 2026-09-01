"""记忆辅助存储层 —— SQLite 摘要与用户画像。

存储职责：
- summaries: 短期记忆的历史摘要（滑动窗口溢出部分的压缩）
- user_profiles: 实体记忆（用户画像）

原始消息归档已经迁移至 PostgreSQL ``messages`` 表，由 ``services.persistence``
异步读写；长期记忆向量由 memory_store.py（ChromaDB）负责。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from services.checkpoint import get_meta_conn

log = logging.getLogger(__name__)

# ─── 滑动窗口配置 ──────────────────────────────────────────────────────────
SLIDING_WINDOW_SIZE = 8   # 发送给 LLM 的最近消息条数
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


# ─── 数据库初始化 ──────────────────────────────────────────────────────────
def init_memory_tables() -> None:
    """
    函数作用：
        创建记忆系统所需的数据库表（幂等操作）。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    conn = get_meta_conn()
    conn.executescript("""
        -- 短期记忆：历史摘要（每个 thread 一条，累积更新）
        CREATE TABLE IF NOT EXISTS summaries (
            thread_id   TEXT PRIMARY KEY,
            summary     TEXT NOT NULL DEFAULT '',
            msg_count   INTEGER NOT NULL DEFAULT 0,
            updated_at  INTEGER NOT NULL
        );

        -- 实体记忆：用户画像
        CREATE TABLE IF NOT EXISTS user_profiles (
            thread_id   TEXT PRIMARY KEY,
            profile     TEXT NOT NULL DEFAULT '{}',
            updated_at  INTEGER NOT NULL
        );
    """)
    conn.commit()
    log.info("记忆系统数据表初始化完成")


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
    conn = get_meta_conn()
    conn.execute(
        """INSERT OR REPLACE INTO summaries (thread_id, summary, msg_count, updated_at)
           VALUES (?, ?, ?, ?)""",
        (thread_id, summary, msg_count, int(time.time())),
    )
    conn.commit()


def get_summary(thread_id: str) -> Optional[str]:
    """
    函数作用：
        获取对话的历史摘要，不存在则返回 None。
    输入参数：
        - thread_id: str
    输出参数：
        - Optional[str]
    """
    conn = get_meta_conn()
    cur = conn.execute(
        "SELECT summary FROM summaries WHERE thread_id = ?", (thread_id,)
    )
    row = cur.fetchone()
    return row[0] if row else None


def get_summary_msg_count(thread_id: str) -> int:
    """
    函数作用：
        获取摘要已覆盖的消息数量。
    输入参数：
        - thread_id: str
    输出参数：
        - int
    """
    conn = get_meta_conn()
    cur = conn.execute(
        "SELECT msg_count FROM summaries WHERE thread_id = ?", (thread_id,)
    )
    row = cur.fetchone()
    return row[0] if row else 0


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
    conn = get_meta_conn()
    conn.execute(
        """INSERT OR REPLACE INTO user_profiles (thread_id, profile, updated_at)
           VALUES (?, ?, ?)""",
        (thread_id, json.dumps(profile, ensure_ascii=False), int(time.time())),
    )
    conn.commit()


def get_user_profile(thread_id: str) -> Optional[dict]:
    """
    函数作用：
        获取用户画像，不存在则返回 None。
    输入参数：
        - thread_id: str
    输出参数：
        - Optional[dict]
    """
    conn = get_meta_conn()
    cur = conn.execute(
        "SELECT profile FROM user_profiles WHERE thread_id = ?", (thread_id,)
    )
    row = cur.fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None
