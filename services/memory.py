"""记忆存储层 —— 基于 SQLite 的持久化存储，支撑四层记忆架构。

存储职责：
- messages_archive: 长期记忆的原始消息归档（完整保留所有对话消息）
- summaries: 短期记忆的历史摘要（滑动窗口溢出部分的压缩）
- user_profiles: 实体记忆（用户画像）

注意：长期记忆的向量化存储由 memory_store.py（ChromaDB）负责，
本模块只负责 SQLite 侧的结构化存储。
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
        -- 长期记忆：完整消息归档
        CREATE TABLE IF NOT EXISTS messages_archive (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id   TEXT NOT NULL,
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            msg_index   INTEGER NOT NULL,
            created_at  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_thread
            ON messages_archive(thread_id, msg_index);

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


# ─── 消息归档（长期记忆原始存储） ──────────────────────────────────────────
def save_messages(thread_id: str, messages: list[dict]) -> None:
    """
    函数作用：
        将消息列表完整归档到 SQLite。
    输入参数：
        - thread_id: str
        - messages: list[dict]
    输出参数：
        - 无
    """
    conn = get_meta_conn()
    now = int(time.time())
    # 获取当前最大 msg_index
    cur = conn.execute(
        "SELECT COALESCE(MAX(msg_index), -1) FROM messages_archive WHERE thread_id = ?",
        (thread_id,),
    )
    max_idx = cur.fetchone()[0]

    for i, msg in enumerate(messages):
        conn.execute(
            "INSERT INTO messages_archive (thread_id, role, content, msg_index, created_at) VALUES (?, ?, ?, ?, ?)",
            (thread_id, msg["role"], msg["content"], max_idx + 1 + i, now),
        )
    conn.commit()


def load_all_messages(thread_id: str) -> list[dict]:
    """
    函数作用：
        加载指定对话的完整消息列表（按顺序）。
    输入参数：
        - thread_id: str
    输出参数：
        - list[dict]
    """
    conn = get_meta_conn()
    cur = conn.execute(
        "SELECT role, content FROM messages_archive WHERE thread_id = ? ORDER BY msg_index ASC",
        (thread_id,),
    )
    return [{"role": row[0], "content": row[1]} for row in cur.fetchall()]


def get_archived_message_count(thread_id: str) -> int:
    """
    函数作用：
        获取已归档的消息总数。
    输入参数：
        - thread_id: str
    输出参数：
        - int
    """
    conn = get_meta_conn()
    cur = conn.execute(
        "SELECT COUNT(*) FROM messages_archive WHERE thread_id = ?",
        (thread_id,),
    )
    return cur.fetchone()[0]


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
