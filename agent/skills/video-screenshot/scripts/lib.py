#!/usr/bin/env python3
"""Compatibility helpers for the video-screenshot skill.

The product implementation lives in `services.evidence_video`.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.evidence_video import (  # noqa: F401
    EvidenceVideoDependencyError,
    UnsupportedVideoError,
    VideoExtractOptions,
    extract_video_evidence,
    load_evidence_report,
    save_video_upload,
)
