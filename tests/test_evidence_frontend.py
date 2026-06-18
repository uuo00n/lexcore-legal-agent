from __future__ import annotations

from pathlib import Path


def test_frontend_upload_accepts_video_and_calls_evidence_endpoint():
    root = Path(__file__).resolve().parents[1]
    index = (root / "static" / "index.html").read_text(encoding="utf-8")
    app = (root / "static" / "app.js").read_text(encoding="utf-8")

    assert ".mp4" in index
    assert ".mov" in index
    assert "/api/evidence/video/extract" in app
    assert "evidence_id" in app
