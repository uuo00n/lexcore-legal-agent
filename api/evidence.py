"""证据材料 API。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from services.evidence_video import (
    EvidenceVideoDependencyError,
    UnsupportedVideoError,
    VideoExtractOptions,
    evidence_file_path,
    extract_video_evidence,
    load_evidence_report,
    save_evidence_report,
    save_video_upload,
)
from services.task_queue import submit_task


router = APIRouter()


@router.post("/evidence/video/extract")
async def extract_video(
    file: UploadFile = File(...),
    strategy: str = Form("scene"),
    interval_seconds: float = Form(1.0),
    scene_threshold: float = Form(0.10),
    sample_interval: float = Form(2.0),
):
    """上传视频并创建抽帧任务。"""
    try:
        raw = await file.read()
        saved = save_video_upload(file.filename or "", raw)
        options = VideoExtractOptions(
            strategy=strategy,
            interval_seconds=interval_seconds,
            scene_threshold=scene_threshold,
            sample_interval=sample_interval,
        )
    except UnsupportedVideoError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    async def _factory():
        try:
            return await asyncio.to_thread(
                extract_video_evidence,
                saved.source_path,
                output_dir=saved.frames_dir,
                evidence_id=saved.evidence_id,
                filename=saved.filename,
                options=options,
            )
        except EvidenceVideoDependencyError as exc:
            result = {
                "status": "dependency_missing",
                "evidence_id": saved.evidence_id,
                "filename": saved.filename,
                "missing": exc.missing,
                "message": str(exc),
            }
            save_evidence_report(saved.evidence_id, result)
            return result

    task_id = submit_task("video_screenshot", _factory)
    return {
        "evidence_id": saved.evidence_id,
        "filename": saved.filename,
        "task_id": task_id,
        "status_url": f"/api/tasks/{task_id}",
        "report_url": f"/api/evidence/{saved.evidence_id}",
    }


@router.get("/evidence/{evidence_id}")
async def get_evidence_report(evidence_id: str):
    """返回视频证据处理报告。"""
    try:
        return load_evidence_report(evidence_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/evidence/{evidence_id}/files/{relative_path:path}")
async def download_evidence_file(evidence_id: str, relative_path: str):
    """下载证据处理产物，如帧图片。"""
    try:
        path = evidence_file_path(evidence_id, relative_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "evidence file not found") from exc
    media_type = "image/jpeg" if Path(path).suffix.lower() in {".jpg", ".jpeg"} else "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=Path(path).name)
