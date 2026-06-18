"""后台可观测性 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from services.auth import require_admin
from services.observability import (
    dashboard_summary,
    eval_trends,
    get_trace,
    get_trace_timeline,
    list_eval_runs,
    list_llm_calls,
    list_traces,
)
from services.quota import list_quota_usage


router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/admin/summary")
async def admin_summary():
    """
    函数作用：
        返回后台看板汇总指标。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    return dashboard_summary()


@router.get("/admin/traces")
async def admin_traces(limit: int = Query(default=30, ge=1, le=200)):
    """
    函数作用：
        返回最近 Agent trace 列表。
    输入参数：
        - limit: int，默认值 30
    输出参数：
        - 未标注
    """
    return {"items": list_traces(limit=limit)}


@router.get("/admin/traces/{trace_id}")
async def admin_trace_detail(trace_id: str):
    """
    函数作用：
        返回单条 Agent trace 详情。
    输入参数：
        - trace_id: str
    输出参数：
        - 未标注
    """
    trace = get_trace(trace_id)
    if trace is None:
        raise HTTPException(404, "trace not found")
    return trace


@router.get("/admin/traces/{trace_id}/timeline")
async def admin_trace_timeline(trace_id: str):
    """
    函数作用：
        返回适合前端展示的 trace 时间线。
    输入参数：
        - trace_id: str
    输出参数：
        - 未标注
    """
    timeline = get_trace_timeline(trace_id)
    if timeline is None:
        raise HTTPException(404, "trace not found")
    return timeline


@router.get("/admin/llm-calls")
async def admin_llm_calls(limit: int = Query(default=30, ge=1, le=200)):
    """
    函数作用：
        返回最近 LLM 调用日志。
    输入参数：
        - limit: int，默认值 30
    输出参数：
        - 未标注
    """
    return {"items": list_llm_calls(limit=limit)}


@router.get("/admin/eval-runs")
async def admin_eval_runs(limit: int = Query(default=20, ge=1, le=100)):
    """
    函数作用：
        返回最近评测历史。
    输入参数：
        - limit: int，默认值 20
    输出参数：
        - 未标注
    """
    return {"items": list_eval_runs(limit=limit)}


@router.get("/admin/eval-trends")
async def admin_eval_trends(limit: int = Query(default=20, ge=1, le=100)):
    """
    函数作用：
        返回评测指标趋势。
    输入参数：
        - limit: int，默认值 20
    输出参数：
        - 未标注
    """
    return eval_trends(limit=limit)


@router.get("/admin/quota")
async def admin_quota(limit: int = Query(default=30, ge=1, le=200)):
    """
    函数作用：
        返回每日配额使用情况。
    输入参数：
        - limit: int，默认值 30
    输出参数：
        - 未标注
    """
    return {"items": list_quota_usage(limit=limit)}
