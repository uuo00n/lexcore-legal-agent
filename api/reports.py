"""报告生成 API。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from services.checkpoint import load_doc
from services.contract_report import REPORT_DIR, save_contract_report
from services.task_queue import get_task, list_tasks, submit_task


router = APIRouter()


class ContractReportRequest(BaseModel):
    """合同审查报告请求。"""
    doc_id: str
    focus: Optional[str] = ""


@router.post("/reports/contract")
async def create_contract_report(req: ContractReportRequest):
    """
    函数作用：
        基于已上传文档生成合同审查 Markdown 报告。
    输入参数：
        - req: ContractReportRequest
    输出参数：
        - 未标注
    """
    doc = load_doc(req.doc_id)
    if doc is None:
        raise HTTPException(404, f"doc_id {req.doc_id} not found")
    result = save_contract_report(doc["filename"], doc["text"], req.focus or "")
    return {
        "report_id": result["report_id"],
        "download_url": f"/api/reports/{result['report_id']}",
        "markdown": result["markdown"],
    }


@router.post("/reports/contract/tasks")
async def create_contract_report_task(req: ContractReportRequest):
    """
    函数作用：
        创建异步合同审查报告任务。
    输入参数：
        - req: ContractReportRequest
    输出参数：
        - 未标注
    """
    doc = load_doc(req.doc_id)
    if doc is None:
        raise HTTPException(404, f"doc_id {req.doc_id} not found")

    async def _factory():
        result = save_contract_report(doc["filename"], doc["text"], req.focus or "")
        return {
            "report_id": result["report_id"],
            "download_url": f"/api/reports/{result['report_id']}",
        }

    task_id = submit_task("contract_report", _factory)
    return {"task_id": task_id, "status_url": f"/api/tasks/{task_id}"}


@router.get("/reports/{report_id}")
async def download_report(report_id: str):
    """
    函数作用：
        下载生成的 Markdown 报告。
    输入参数：
        - report_id: str
    输出参数：
        - 未标注
    """
    if "/" in report_id or ".." in report_id:
        raise HTTPException(400, "invalid report_id")
    path = REPORT_DIR / f"{report_id}.md"
    if not path.exists():
        raise HTTPException(404, "report not found")
    return FileResponse(
        Path(path),
        media_type="text/markdown; charset=utf-8",
        filename=f"{report_id}.md",
    )


@router.get("/tasks")
async def get_tasks():
    """
    函数作用：
        返回最近异步任务。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    return {"items": list_tasks()}


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """
    函数作用：
        查询异步任务状态。
    输入参数：
        - task_id: str
    输出参数：
        - 未标注
    """
    task = get_task(task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    return task
