"""LangGraph 内存 checkpoint + SQLite 上传文档元数据。

注意：使用 MemorySaver 替代 SqliteSaver 以兼容异步 astream_events。
LangGraph 的 MemorySaver 同时支持同步和异步操作。
会话元数据已迁移至 PostgreSQL ``conversations`` 表。
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from langgraph.checkpoint.memory import MemorySaver


_checkpointer: Optional[MemorySaver] = None
_meta_conn: Optional[sqlite3.Connection] = None


def _ensure_parent(path: str | Path) -> Path:
    """
    函数作用：
        待补充。
    输入参数：
        - path: str | Path
    输出参数：
        - Path
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def init_checkpointer(db_path: str | None = None) -> MemorySaver:
    """
    函数作用：
        初始化 LangGraph checkpointer（内存版，支持异步）。
    输入参数：
        - db_path: str | None，默认值 None
    输出参数：
        - MemorySaver
    """
    global _checkpointer
    _checkpointer = MemorySaver()
    return _checkpointer


def get_checkpointer() -> MemorySaver:
    """
    函数作用：
        待补充。
    输入参数：
        - 无
    输出参数：
        - MemorySaver
    """
    if _checkpointer is None:
        raise RuntimeError("checkpointer not initialized; call init_checkpointer() first")
    return _checkpointer


def init_meta_db(db_path: str | None = None) -> sqlite3.Connection:
    """
    函数作用：
        初始化辅助元数据库（仅上传文档及其他未迁移模块）。
    输入参数：
        - db_path: str | None，默认值 None
    输出参数：
        - sqlite3.Connection
    """
    global _meta_conn
    db_path = db_path or os.getenv("DOCS_DB", "data/docs.sqlite")
    _ensure_parent(db_path)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS docs (
            doc_id     TEXT PRIMARY KEY,
            filename   TEXT NOT NULL,
            text       TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            truncated  INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    _meta_conn = conn
    return conn


def get_meta_conn() -> sqlite3.Connection:
    """
    函数作用：
        待补充。
    输入参数：
        - 无
    输出参数：
        - sqlite3.Connection
    """
    if _meta_conn is None:
        raise RuntimeError("meta db not initialized; call init_meta_db() first")
    return _meta_conn


def save_doc(doc_id: str, filename: str, text: str, truncated: bool) -> None:
    """
    函数作用：
        待补充。
    输入参数：
        - doc_id: str
        - filename: str
        - text: str
        - truncated: bool
    输出参数：
        - 无
    """
    conn = get_meta_conn()
    conn.execute(
        "INSERT INTO docs (doc_id, filename, text, char_count, truncated, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, filename, text, len(text), 1 if truncated else 0, int(time.time())),
    )
    conn.commit()


def load_doc(doc_id: str) -> Optional[dict]:
    """
    函数作用：
        待补充。
    输入参数：
        - doc_id: str
    输出参数：
        - Optional[dict]
    """
    conn = get_meta_conn()
    cur = conn.execute(
        "SELECT doc_id, filename, text, char_count, truncated FROM docs WHERE doc_id = ?",
        (doc_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "doc_id": row[0],
        "filename": row[1],
        "text": row[2],
        "char_count": row[3],
        "truncated": bool(row[4]),
    }


def reset_for_tests() -> None:
    """
    函数作用：
        仅供测试使用，清空全局单例。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    global _checkpointer, _meta_conn
    if _meta_conn is not None:
        _meta_conn.close()
    _checkpointer = None
    _meta_conn = None
