from __future__ import annotations

from pathlib import Path

import pytest


def test_save_video_upload_rejects_unsupported_extension(tmp_path):
    from services.evidence_video import UnsupportedVideoError, save_video_upload

    with pytest.raises(UnsupportedVideoError):
        save_video_upload("材料.txt", b"not a video", root=tmp_path)


def test_save_video_upload_writes_source_file(tmp_path):
    from services.evidence_video import save_video_upload

    saved = save_video_upload("聊天录屏.MP4", b"video-bytes", root=tmp_path)

    assert saved.evidence_id
    assert saved.filename == "聊天录屏.MP4"
    assert saved.source_path.exists()
    assert saved.source_path.read_bytes() == b"video-bytes"
    assert saved.source_path.parent == tmp_path / saved.evidence_id


def test_extract_video_evidence_reports_missing_ffmpeg(tmp_path, monkeypatch):
    from services.evidence_video import EvidenceVideoDependencyError, extract_video_evidence

    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    monkeypatch.setenv("PATH", "")

    with pytest.raises(EvidenceVideoDependencyError) as exc:
        extract_video_evidence(source, output_dir=tmp_path / "frames")

    assert "ffmpeg" in exc.value.missing
    assert "ffprobe" in exc.value.missing


@pytest.mark.asyncio
async def test_video_extract_api_submits_task(tmp_path, monkeypatch):
    from api.evidence import extract_video
    from services.evidence_video import load_evidence_report

    submitted = {}

    def fake_submit_task(name, factory):
        submitted["name"] = name
        submitted["factory"] = factory
        return "task-123"

    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setattr("api.evidence.submit_task", fake_submit_task)

    class FakeUpload:
        filename = "recording.mp4"

        async def read(self):
            return b"video-bytes"

    result = await extract_video(FakeUpload())

    assert result["evidence_id"]
    assert result["task_id"] == "task-123"
    assert result["status_url"] == "/api/tasks/task-123"
    assert submitted["name"] == "video_screenshot"
    assert (tmp_path / result["evidence_id"] / "source.mp4").exists()

    initial = load_evidence_report(result["evidence_id"], root=tmp_path)
    assert initial["status"] == "queued"


@pytest.mark.asyncio
async def test_video_extract_task_persists_dependency_missing_report(tmp_path, monkeypatch):
    from api.evidence import extract_video
    from services.evidence_video import EvidenceVideoDependencyError, load_evidence_report

    submitted = {}

    def fake_submit_task(name, factory):
        submitted["factory"] = factory
        return "task-456"

    def fake_extract(*args, **kwargs):
        raise EvidenceVideoDependencyError(["ffmpeg", "ffprobe"])

    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setattr("api.evidence.submit_task", fake_submit_task)
    monkeypatch.setattr("api.evidence.extract_video_evidence", fake_extract)

    class FakeUpload:
        filename = "recording.mp4"

        async def read(self):
            return b"video-bytes"

    result = await extract_video(FakeUpload())
    task_result = await submitted["factory"]()
    report = load_evidence_report(result["evidence_id"], root=tmp_path)

    assert task_result["status"] == "dependency_missing"
    assert report["status"] == "dependency_missing"
    assert report["missing"] == ["ffmpeg", "ffprobe"]


def test_chat_state_loads_evidence_summary(tmp_path, monkeypatch):
    evidence_id = "a" * 32
    evidence_dir = tmp_path / evidence_id
    evidence_dir.mkdir()
    (evidence_dir / "_report.json").write_text(
        """
        {
          "status": "success",
          "evidence_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "filename": "recording.mp4",
          "duration_seconds": 12.5,
          "strategy": "scene",
          "frames": [
            {
              "filename": "frames/frame_001_00m00s.jpg",
              "capture_time_seconds": 0,
              "sha256": "abc123"
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path))

    from api.chat import ChatRequest, _build_state_input

    req = ChatRequest(
        thread_id="thread-evidence",
        message="帮我分析这段聊天录屏证据",
        evidence_id=evidence_id,
    )
    state = _build_state_input(
        graph=object(),
        req=req,
        doc_text=None,
        doc_name=None,
        trace_id="trace-1",
    )

    assert state["uploaded_evidence_id"] == evidence_id
    assert "recording.mp4" in state["uploaded_evidence_text"]
    assert "frame_001_00m00s.jpg" in state["uploaded_evidence_text"]
