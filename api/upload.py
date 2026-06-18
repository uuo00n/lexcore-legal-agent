"""文件上传接口。"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile

from services.checkpoint import save_doc
from services.doc_parser import SUPPORTED_EXTS, UnsupportedDocumentError, parse_document


log = logging.getLogger(__name__)
router = APIRouter()


def _upload_dir() -> Path:
    """
    函数作用：
        待补充。
    输入参数：
        - 无
    输出参数：
        - Path
    """
    p = Path(os.getenv("UPLOAD_DIR", "data/uploads"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _max_bytes() -> int:
    """
    函数作用：
        待补充。
    输入参数：
        - 无
    输出参数：
        - int
    """
    return int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    """
    函数作用：
        待补充。
    输入参数：
        - file: UploadFile，默认值 File(...)
    输出参数：
        - 未标注
    """
    if not file.filename:
        raise HTTPException(400, "no filename")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTS:
        raise HTTPException(400, f"unsupported file type: {suffix}, allowed: {sorted(SUPPORTED_EXTS)}")

    raw = await file.read()
    if len(raw) > _max_bytes():
        raise HTTPException(413, f"file too large; max {_max_bytes() // (1024 * 1024)} MB")
    if len(raw) == 0:
        raise HTTPException(400, "empty file")

    doc_id = uuid4().hex
    save_path = _upload_dir() / f"{doc_id}{suffix}"
    save_path.write_bytes(raw)

    try:
        text, truncated = parse_document(save_path)
    except UnsupportedDocumentError as exc:
        save_path.unlink(missing_ok=True)
        raise HTTPException(400, str(exc))
    except Exception as exc:
        log.exception("parse failed")
        save_path.unlink(missing_ok=True)
        raise HTTPException(500, f"failed to parse document: {exc}")

    if not text.strip():
        save_path.unlink(missing_ok=True)
        raise HTTPException(422, "document parsed empty; please check the file")

    save_doc(doc_id, file.filename, text, truncated)

    return {
        "doc_id": doc_id,
        "filename": file.filename,
        "char_count": len(text),
        "truncated": truncated,
    }
