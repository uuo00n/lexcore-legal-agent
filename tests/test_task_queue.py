from __future__ import annotations

import asyncio

import pytest

from services.task_queue import get_task, reset_tasks_for_tests, submit_task


def setup_function():
    reset_tasks_for_tests()


@pytest.mark.asyncio
async def test_task_queue_runs_async_task():
    async def factory():
        return {"ok": True}

    task_id = submit_task("demo", factory)
    for _ in range(20):
        task = get_task(task_id)
        if task and task["status"] == "success":
            break
        await asyncio.sleep(0.01)

    task = get_task(task_id)
    assert task is not None
    assert task["status"] == "success"
    assert task["result"] == {"ok": True}
