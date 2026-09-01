"""tool_calls 表仓储 —— 替代 SQLite `agent_events` 中的工具事件。

这是「API Key 不能写数据库日志」最关键的一处：工具入参里可能带着得理 OpenAPI 的
app_id / secret、Authorization 头或完整连接串，因此 `input` 与 `output_summary`
必须整体经过 `redact()`。`output_summary` 只应保存摘要（命中数、Top 分数、来源列表），
不要把裁判文书全文或完整检索结果塞进来。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import case, func, select

from infrastructure.models.tool_call import ToolCall
from infrastructure.repositories.base import BaseRepository


class ToolCallRepository(BaseRepository):
    """工具调用明细读写。"""

    async def record_call(
        self,
        trace_id: str,
        agent_name: str,
        tool_name: str,
        *,
        agent_run_id: uuid.UUID | None = None,
        input_payload: dict[str, Any] | None = None,
        output_summary: dict[str, Any] | None = None,
        latency_ms: int = 0,
        success: bool = True,
        error: str = "",
    ) -> ToolCall:
        """
        函数作用：
            记录一次工具调用。入参与输出摘要均脱敏后写入 JSONB。
        输入参数：
            - trace_id: str
            - agent_name: str
            - tool_name: str
            - agent_run_id: uuid.UUID | None，默认值 None
            - input_payload: dict[str, Any] | None，默认值 None
            - output_summary: dict[str, Any] | None，默认值 None
            - latency_ms: int，默认值 0
            - success: bool，默认值 True
            - error: str，默认值 ""
        输出参数：
            - ToolCall
        """
        call = ToolCall(
            trace_id=trace_id,
            agent_run_id=agent_run_id,
            agent_name=agent_name[:64],
            tool_name=tool_name[:64],
            input_payload=self._json(input_payload or {}),
            output_summary=self._json(output_summary or {}),
            latency_ms=max(0, int(latency_ms)),
            success=bool(success),
            error=self._text(error),
        )
        self.session.add(call)
        await self.flush()
        return call

    async def list_by_trace(self, trace_id: str) -> list[ToolCall]:
        """
        函数作用：
            按时间正序取回某次运行的全部工具调用，用于 trace 时间线。
        输入参数：
            - trace_id: str
        输出参数：
            - list[ToolCall]
        """
        stmt = (
            select(ToolCall)
            .where(ToolCall.trace_id == trace_id)
            .order_by(ToolCall.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_recent(
        self,
        *,
        limit: int = 50,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> list[ToolCall]:
        """
        函数作用：
            列出最近的工具调用，可按工具名与成功状态过滤。
        输入参数：
            - limit: int，默认值 50
            - tool_name: str | None，默认值 None
            - success: bool | None，默认值 None
        输出参数：
            - list[ToolCall]
        """
        stmt = select(ToolCall)
        if tool_name:
            stmt = stmt.where(ToolCall.tool_name == tool_name)
        if success is not None:
            stmt = stmt.where(ToolCall.success.is_(success))
        stmt = stmt.order_by(ToolCall.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars())

    async def stats_by_tool(self) -> list[dict[str, Any]]:
        """
        函数作用：
            按工具聚合调用次数、成功数与平均耗时，供后台看板展示工具健康度。
        输入参数：
            - 无
        输出参数：
            - list[dict[str, Any]]，按调用次数倒序
        """
        stmt = (
            select(
                ToolCall.tool_name,
                func.count(ToolCall.id),
                func.sum(case((ToolCall.success.is_(True), 1), else_=0)),
                func.avg(ToolCall.latency_ms),
            )
            .group_by(ToolCall.tool_name)
            .order_by(func.count(ToolCall.id).desc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "tool_name": str(tool_name),
                "calls": int(calls),
                "success": int(success or 0),
                "avg_latency_ms": int(avg or 0),
            }
            for tool_name, calls, success, avg in rows
        ]


__all__ = ["ToolCallRepository"]
