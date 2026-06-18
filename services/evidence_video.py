"""视频证据处理服务。

负责保存用户上传的视频、调用 ffmpeg 抽帧、做轻量去重，并生成可追溯的
`_report.json`。本模块是 `agent/skills/video-screenshot` 的产品化执行层。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageOps


VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".ts"}
REPORT_NAME = "_report.json"
DEFAULT_MAX_UPLOAD_MB = 200


class UnsupportedVideoError(ValueError):
    """上传文件不是支持的视频格式。"""


class EvidenceVideoDependencyError(RuntimeError):
    """本地缺少处理视频所需的系统依赖。"""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(f"missing video evidence dependencies: {', '.join(missing)}")


@dataclass(frozen=True)
class SavedEvidenceVideo:
    evidence_id: str
    filename: str
    source_path: Path
    evidence_dir: Path

    @property
    def frames_dir(self) -> Path:
        return self.evidence_dir / "frames"


@dataclass(frozen=True)
class VideoExtractOptions:
    strategy: str = "scene"
    interval_seconds: float = 1.0
    scene_threshold: float = 0.10
    sample_interval: float = 2.0
    dedup_threshold: int = 4
    max_size: int = 0
    quality: int = 2
    timeout_seconds: float = 1800


def evidence_root() -> Path:
    root = Path(os.getenv("EVIDENCE_DIR", "data/evidence"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def max_upload_bytes() -> int:
    return int(os.getenv("MAX_VIDEO_UPLOAD_MB", str(DEFAULT_MAX_UPLOAD_MB))) * 1024 * 1024


def save_video_upload(filename: str, content: bytes, *, root: str | Path | None = None) -> SavedEvidenceVideo:
    """保存上传视频到独立证据目录，并写入 queued 初始报告。"""
    if not filename:
        raise UnsupportedVideoError("missing filename")
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_EXTS:
        raise UnsupportedVideoError(f"unsupported video type: {suffix}")
    if not content:
        raise UnsupportedVideoError("empty video file")
    if len(content) > max_upload_bytes():
        raise UnsupportedVideoError(f"video too large; max {max_upload_bytes() // (1024 * 1024)} MB")

    base = Path(root) if root is not None else evidence_root()
    evidence_id = uuid4().hex
    evidence_dir = base / evidence_id
    evidence_dir.mkdir(parents=True, exist_ok=False)
    source_path = evidence_dir / f"source{suffix}"
    source_path.write_bytes(content)

    saved = SavedEvidenceVideo(
        evidence_id=evidence_id,
        filename=filename,
        source_path=source_path,
        evidence_dir=evidence_dir,
    )
    _write_report(saved.evidence_dir, {
        "status": "queued",
        "evidence_id": evidence_id,
        "filename": filename,
        "input": str(source_path),
        "frames": [],
        "created_at": int(time.time()),
    })
    return saved


def load_evidence_report(evidence_id: str, *, root: str | Path | None = None) -> dict[str, Any]:
    evidence_dir = _resolve_evidence_dir(evidence_id, root=root)
    report_path = evidence_dir / REPORT_NAME
    if not report_path.exists():
        return {"status": "missing", "evidence_id": evidence_id}
    return json.loads(report_path.read_text(encoding="utf-8"))


def save_evidence_report(evidence_id: str, report: dict[str, Any], *, root: str | Path | None = None) -> None:
    """覆盖写入某个 evidence_id 的处理报告。"""
    evidence_dir = _resolve_evidence_dir(evidence_id, root=root)
    _write_report(evidence_dir, report)


def evidence_file_path(evidence_id: str, relative_path: str, *, root: str | Path | None = None) -> Path:
    evidence_dir = _resolve_evidence_dir(evidence_id, root=root)
    rel = Path(relative_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("invalid evidence file path")
    path = evidence_dir / rel
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))
    return path


def evidence_prompt_summary(evidence_id: str, *, root: str | Path | None = None) -> str:
    report = load_evidence_report(evidence_id, root=root)
    if report.get("status") != "success":
        status = report.get("status") or "unknown"
        return f"用户上传了视频证据（evidence_id={evidence_id}），当前处理状态：{status}。"
    frames = report.get("frames") or []
    preview = frames[:8]
    lines = [
        f"用户上传了视频证据（evidence_id={evidence_id}，文件名：{report.get('filename') or '未知'}）。",
        f"抽帧策略：{report.get('strategy')}；视频时长：{report.get('duration_seconds')} 秒；保留截图：{len(frames)} 张。",
        "该材料是事实/证据上下文，不是法律依据；法条仍需通过法律检索工具确认。",
    ]
    if preview:
        lines.append("前几张截图时间戳与哈希：")
        for item in preview:
            lines.append(
                f"- {item.get('filename')}，约 {item.get('capture_time_seconds')} 秒，SHA256={item.get('sha256')}"
            )
    return "\n".join(lines)


def extract_video_evidence(
    video_path: str | Path,
    *,
    output_dir: str | Path,
    evidence_id: str | None = None,
    filename: str | None = None,
    options: VideoExtractOptions | None = None,
) -> dict[str, Any]:
    """抽取视频截图并生成报告。"""
    source_path = Path(video_path)
    if not source_path.exists():
        raise FileNotFoundError(str(source_path))
    opts = options or VideoExtractOptions()
    _validate_options(opts)
    missing = [name for name in ("ffmpeg", "ffprobe") if _find_tool(name) is None]
    if missing:
        raise EvidenceVideoDependencyError(missing)

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _clean_frames(output)
    info = _probe_video(source_path)

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="legal-video-") as tmp:
        tmpdir = Path(tmp)
        extracted = _run_ffmpeg_extract(source_path, tmpdir, info, opts)
        frames = _deduplicate_and_copy(extracted, output, info, opts)

    report = {
        "status": "success",
        "evidence_id": evidence_id,
        "filename": filename or source_path.name,
        "input": str(source_path),
        "duration_seconds": round(info["duration_seconds"], 3),
        "frame_rate_fps": info.get("frame_rate_fps"),
        "strategy": opts.strategy,
        "options": {
            "interval_seconds": opts.interval_seconds,
            "scene_threshold": opts.scene_threshold,
            "sample_interval": opts.sample_interval,
            "dedup_threshold": opts.dedup_threshold,
            "max_size": opts.max_size,
            "quality": opts.quality,
        },
        "total_extracted": len(extracted),
        "kept_after_dedup": len(frames),
        "dedup_stats": {
            "removed_duplicates": len(extracted) - len(frames),
        },
        "frames": frames,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "updated_at": int(time.time()),
    }
    _write_report(output.parent, report)
    return report


def _resolve_evidence_dir(evidence_id: str, *, root: str | Path | None = None) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", evidence_id or ""):
        raise ValueError("invalid evidence_id")
    base = Path(root) if root is not None else evidence_root()
    return base / evidence_id


def _write_report(evidence_dir: Path, report: dict[str, Any]) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / REPORT_NAME).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for root in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        candidate = Path(root) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _validate_options(opts: VideoExtractOptions) -> None:
    if opts.strategy not in {"scene", "keyframe", "interval", "smart"}:
        raise ValueError("unsupported extraction strategy")
    if opts.interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if opts.quality < 1 or opts.quality > 31:
        raise ValueError("quality must be between 1 and 31")


def _probe_video(path: Path) -> dict[str, Any]:
    cmd = [
        _find_tool("ffprobe") or "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "format=duration:stream=avg_frame_rate,r_frame_rate",
        "-of", "json",
        str(path),
    ]
    completed = subprocess.run(cmd, text=True, capture_output=True, timeout=15, check=True)
    data = json.loads(completed.stdout or "{}")
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    if duration <= 0:
        raise RuntimeError("unable to detect video duration")
    stream = ((data.get("streams") or [{}])[0]) or {}
    fps = _parse_rate(stream.get("avg_frame_rate")) or _parse_rate(stream.get("r_frame_rate"))
    return {"duration_seconds": duration, "frame_rate_fps": fps}


def _parse_rate(value: Any) -> float | None:
    text = str(value or "")
    if not text or text == "0/0":
        return None
    try:
        if "/" in text:
            n, d = text.split("/", 1)
            return float(n) / float(d) if float(d) else None
        return float(text)
    except Exception:
        return None


def _run_ffmpeg_extract(path: Path, tmpdir: Path, info: dict[str, Any], opts: VideoExtractOptions) -> list[Path]:
    output_pattern = tmpdir / ("frame_%06d.jpg" if opts.strategy == "interval" else "frame_%010d.jpg")
    vf = _build_filter(info, opts)
    cmd = [
        _find_tool("ffmpeg") or "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-vf",
        vf,
    ]
    if opts.strategy != "interval":
        cmd.extend(["-vsync", "vfr", "-frame_pts", "1"])
    cmd.extend(["-q:v", str(opts.quality), str(output_pattern)])
    subprocess.run(cmd, text=True, capture_output=True, timeout=opts.timeout_seconds, check=True)
    return sorted(p for p in tmpdir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg"})


def _build_filter(info: dict[str, Any], opts: VideoExtractOptions) -> str:
    scale = ""
    if opts.max_size > 0:
        scale = (
            f",scale='if(gt(iw,ih),min({opts.max_size},iw),-2)':"
            f"'if(gt(iw,ih),-2,min({opts.max_size},ih))'"
        )
    if opts.strategy == "interval":
        return f"fps={1.0 / opts.interval_seconds}{scale},format=yuvj420p"
    if opts.strategy == "keyframe":
        return f"mpdecimate{scale},format=yuvj420p"
    if opts.strategy == "smart":
        return f"mpdecimate{scale},format=yuvj420p"

    expr = f"gt(scene,{float(opts.scene_threshold)})"
    fps = info.get("frame_rate_fps")
    if fps and opts.sample_interval > 0:
        n_frames = max(1, round(float(fps) * opts.sample_interval))
        expr = f"{expr}+not(mod(n\\,{n_frames}))"
    return f"select='{expr}'{scale},format=yuvj420p"


def _clean_frames(output: Path) -> None:
    for pattern in ("frame_*.jpg", "frame_*.jpeg"):
        for path in output.glob(pattern):
            path.unlink(missing_ok=True)


def _deduplicate_and_copy(
    extracted: list[Path],
    output: Path,
    info: dict[str, Any],
    opts: VideoExtractOptions,
) -> list[dict[str, Any]]:
    seen_sha: set[str] = set()
    kept_hashes: list[str] = []
    frames: list[dict[str, Any]] = []
    total = len(extracted)
    for index, frame in enumerate(extracted, start=1):
        content = frame.read_bytes()
        digest = sha256(content).hexdigest()
        dhash = _dhash(content)
        if digest in seen_sha or _is_dhash_duplicate(dhash, kept_hashes, opts.dedup_threshold):
            continue
        seen_sha.add(digest)
        if dhash:
            kept_hashes.append(dhash)
        capture_time = _capture_time(frame, index, total, info, opts)
        name = f"frame_{len(frames) + 1:03d}_{_format_timestamp(capture_time)}.jpg"
        shutil.copy2(frame, output / name)
        frames.append({
            "index": len(frames) + 1,
            "filename": f"frames/{name}",
            "capture_time_seconds": round(capture_time, 3),
            "sha256": digest,
        })
    return frames


def _dhash(image_bytes: bytes, size: int = 8) -> str:
    try:
        img = Image.open(tempfile.SpooledTemporaryFile())
    except Exception:
        img = None
    try:
        from io import BytesIO

        img = Image.open(BytesIO(image_bytes)).convert("L")
        w, h = img.size
        top = int(h * 0.12)
        bottom = max(top + 1, h - int(h * 0.12))
        left = int(w * 0.04)
        right = max(left + 1, w - int(w * 0.04))
        img = ImageOps.autocontrast(img.crop((left, top, right, bottom)))
        img = img.resize((size + 1, size))
        pixels = list(img.getdata())
        bits = 0
        for row in range(size):
            row_start = row * (size + 1)
            for col in range(size):
                if pixels[row_start + col] > pixels[row_start + col + 1]:
                    bits |= 1 << (row * size + col)
        return f"{bits:016x}"
    finally:
        if img is not None:
            img.close()


def _is_dhash_duplicate(current: str, kept: list[str], threshold: int) -> bool:
    if not current or threshold <= 0:
        return False
    for prev in kept[-20:]:
        if (int(current, 16) ^ int(prev, 16)).bit_count() <= threshold:
            return True
    return False


def _capture_time(
    frame: Path,
    index: int,
    total: int,
    info: dict[str, Any],
    opts: VideoExtractOptions,
) -> float:
    if opts.strategy == "interval":
        return float(index - 1) * opts.interval_seconds
    match = re.search(r"(\d+)", frame.stem)
    fps = info.get("frame_rate_fps")
    if match and fps:
        seconds = int(match.group(1)) / float(fps)
        if 0 <= seconds <= float(info["duration_seconds"]) * 1.1:
            return seconds
    if total <= 1:
        return 0.0
    return float(info["duration_seconds"]) * float(index - 1) / float(total - 1)


def _format_timestamp(seconds: float) -> str:
    total = int(max(0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}h{m:02d}m{s:02d}s"
    return f"{m:02d}m{s:02d}s"
