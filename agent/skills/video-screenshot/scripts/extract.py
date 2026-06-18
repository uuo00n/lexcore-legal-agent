#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.evidence_video import VideoExtractOptions, extract_video_evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract evidence screenshots from a video.")
    parser.add_argument("video", help="Input video path")
    parser.add_argument("-o", "--output", required=True, help="Output frames directory")
    parser.add_argument("--strategy", default="scene", choices=["scene", "keyframe", "interval", "smart"])
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--scene-threshold", type=float, default=0.10)
    parser.add_argument("--sample-interval", type=float, default=2.0)
    args = parser.parse_args()

    report = extract_video_evidence(
        args.video,
        output_dir=args.output,
        options=VideoExtractOptions(
            strategy=args.strategy,
            interval_seconds=args.interval_seconds,
            scene_threshold=args.scene_threshold,
            sample_interval=args.sample_interval,
        ),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
