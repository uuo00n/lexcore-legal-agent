"""agent_runs 表仓储 —— Agent 执行状态的权威写入路径。

`plan` 是 Planner 产出的结构化计划，`error` 是异常摘要，两者都可能间接携带请求上下文，
因此一律经 `BaseRepository._json` / `._text` 脱敏后入库。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from infrastructure.models.agent_run import (
    AGENT_RUN_STATUSES,
    RUN_RUNNING,
    RUN_SUCCESS,
    AgentRun,
)
from infrastructure.models.base import utcnow
from infrastructure.repositories.base import BaseRepository


def _elapsed_ms(started_at: datetime, finished_at: datetime) -> int:
    """
    函数作用：
        计算耗时毫秒。started_at 若来自不带时区的测试替身，则按 UTC 处理。
    输入参数：
        - started_at: datetime
        - finished_at: datetime
    输出参数：
        - int
    """
    start = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
    end = finished_at if finished_at.tzinfo else finished_at.replace(tzinfo=timezone.utc)
    return max(0, int((end - start).total_seconds() * 1000))


class AgentRunRepository(BaseRepository):
    """Agent 运行轨迹读写。"""

    async def create_run(
        self,
        trace_id: str,
        thread_id: str,
        *,
        conversation_id: uuid.UUID | None = None,
        intent: str = "",
        plan: dict[str, Any] | None = None,
        started_at: datetime | None = None,
    ) -> AgentRun:
        """
        函数作用：
            开启一次运行记录，状态置为 running。
        输入参数：
            - trace_id: str
            - thread_id: str
            - conversation_id: uuid.UUID | None，默认值 None
            - intent: str，默认值 ""
            - plan: dict[str, Any] | None，默认值 None
            - started_at: datetime | None，默认值 None 表示当前 UTC 时间
        输出参数：
            - AgentRun
        """
        run = AgentRun(
            trace_id=trace_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
            status=RUN_RUNNING,
            intent=intent[:64],
            plan=self._json(plan or {}),
            started_at=started_at or utcnow(),
        )
        self.session.add(run)
        await self.flush()
        return run

    async def get_by_trace_id(self, trace_id: str) -> AgentRun | None:
        """
        函数作用：
            按 trace_id 查运行记录。
        输入参数：
            - trace_id: str
        输出参数：
            - AgentRun | None
        """
        stmt = select(AgentRun).where(AgentRun.trace_id == trace_id).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def update_plan(
        self, trace_id: str, plan: dict[str, Any] | None, *, intent: str | None = None
    ) -> AgentRun | None:
        """
        函数作用：
            Planner 产出计划后回填 plan（及意图），运行中可多次调用。
        输入参数：
            - trace_id: str
            - plan: dict[str, Any]
            - intent: str | None，默认值 None 表示不改意图
        输出参数：
            - AgentRun | None
        """
        run = await self.get_by_trace_id(trace_id)
        if run is None:
            return None
        if plan is not None:
            run.plan = self._json(plan)
        if intent is not None:
            run.intent = intent[:64]
        await self.flush()
        return run

    async def complete_run(
        self,
        trace_id: str,
        *,
        status: str = RUN_SUCCESS,
        error: str = "",
        finished_at: datetime | None = None,
    ) -> AgentRun | None:
        """
        函数作用：
            结束一次运行，写入终态、结束时间与冗余耗时列。
        输入参数：
            - trace_id: str
            - status: str，默认值 "success"
            - error: str，默认值 ""
            - finished_at: datetime | None，默认值 None 表示当前 UTC 时间
        输出参数：
            - AgentRun | None
        """
        if status not in AGENT_RUN_STATUSES:
            raise ValueError(f"unsupported run status: {status!r}")
        run = await self.get_by_trace_id(trace_id)
        if run is None:
            return None
        ended = finished_at or utcnow()
        run.status = status
        run.finished_at = ended
        run.latency_ms = _elapsed_ms(run.started_at, ended)
        run.error = self._text(error)
        await self.flush()
        return run

    async def list_recent(
        self,
        *,
        limit: int = 50,
        status: str | None = None,
        thread_id: str | None = None,
    ) -> list[AgentRun]:
        """
        函数作用：
            列出最近运行，供后台 trace 列表使用。
        输入参数：
            - limit: int，默认值 50
            - status: str | None，默认值 None
            - thread_id: str | None，默认值 None
        输出参数：
            - list[AgentRun]
        """
        stmt = select(AgentRun)
        if status:
            stmt = stmt.where(AgentRun.status == status)
        if thread_id:
            stmt = stmt.where(AgentRun.thread_id == thread_id)
        stmt = stmt.order_by(AgentRun.started_at.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars())

    async def summary(self) -> dict[str, Any]:
        """
        函数作用：
            汇总运行总数、各状态计数与平均耗时，对齐旧 dashboard_summary 的口径。
        输入参数：
            - 无
        输出参数：
            - dict[str, Any]
        """
        stmt = select(AgentRun.status, func.count(AgentRun.id), func.avg(AgentRun.latency_ms)).group_by(
            AgentRun.status
        )
        rows = (await self.session.execute(stmt)).all()
        by_status = {str(status): int(count) for status, count, _ in rows}
        total = sum(by_status.values())
        finished = [(count, avg) for status, count, avg in rows if status != RUN_RUNNING and avg]
        weighted = sum(count * float(avg) for count, avg in finished)
        finished_count = sum(count for count, _ in finished)
        return {
            "total": total,
            "by_status": by_status,
            "avg_latency_ms": int(weighted / finished_count) if finished_count else 0,
        }


__all__ = ["AgentRunRepository"]
