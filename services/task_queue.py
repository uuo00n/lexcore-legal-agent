"""进程内异步任务队列。

用于长任务第一版：合同审查报告、后续批量评测、文档索引等。任务状态保存在
内存中，服务重启会丢失；如果要做生产级持久化，可替换成 PostgreSQL/RQ/Celery。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Awaitable, Callable


_tasks: dict[str, dict[str, Any]] = {}


def submit_task(name: str, factory: Callable[[], Awaitable[Any]]) -> str:
    """
    函数作用：
        提交异步任务并返回 task_id。
    输入参数：
        - name: str
        - factory: Callable[[], Awaitable[Any]]
    输出参数：
        - str
    """
    task_id = uuid.uuid4().hex[:16]
    _tasks[task_id] = {
        "task_id": task_id,
        "name": name,
        "status": "queued",
        "result": None,
        "error": "",
        "created_at": int(time.time() * 1000),
        "updated_at": int(time.time() * 1000),
    }
    asyncio.create_task(_run_task(task_id, factory))
    return task_id


async def _run_task(task_id: str, factory: Callable[[], Awaitable[Any]]) -> None:
    """
    函数作用：
        执行任务并更新状态。
    输入参数：
        - task_id: str
        - factory: Callable[[], Awaitable[Any]]
    输出参数：
        - None
    """
    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["updated_at"] = int(time.time() * 1000)
    try:
        result = await factory()
        _tasks[task_id]["status"] = "success"
        _tasks[task_id]["result"] = result
    except Exception as exc:
        _tasks[task_id]["status"] = "error"
        _tasks[task_id]["error"] = str(exc)
    finally:
        _tasks[task_id]["updated_at"] = int(time.time() * 1000)


def get_task(task_id: str) -> dict[str, Any] | None:
    """
    函数作用：
        查询任务状态。
    输入参数：
        - task_id: str
    输出参数：
        - dict[str, Any] | None
    """
    return _tasks.get(task_id)


def list_tasks(limit: int = 50) -> list[dict[str, Any]]:
    """
    函数作用：
        查询最近任务。
    输入参数：
        - limit: int，默认值 50
    输出参数：
        - list[dict[str, Any]]
    """
    return sorted(_tasks.values(), key=lambda item: item["created_at"], reverse=True)[:limit]


def reset_tasks_for_tests() -> None:
    """
    函数作用：
        测试时清空任务状态。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    _tasks.clear()
