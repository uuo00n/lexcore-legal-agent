#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def ocr_pdf(
    input_pdf: str | Path,
    output_pdf: str | Path,
    *,
    language: str = "chi_sim+eng",
    deskew: bool = True,
    rotate_pages: bool = True,
) -> dict[str, Any]:
    input_path = Path(input_pdf)
    output_path = Path(output_pdf)
    result: dict[str, Any] = {
        "source_path": str(input_path),
        "output_path": str(output_path),
        "ocr_performed": False,
        "returncode": None,
        "warnings": [],
    }

    if not input_path.exists():
        result["warnings"].append("file_not_found")
        return result

    executable = shutil.which("ocrmypdf")
    if not executable:
        result["warnings"].append("ocrmypdf_not_installed")
        return result

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [executable, "-l", language, "--skip-text"]
    if deskew:
        cmd.append("--deskew")
    if rotate_pages:
        cmd.append("--rotate-pages")
    cmd.extend([str(input_path), str(output_path)])

    completed = subprocess.run(cmd, text=True, capture_output=True, check=False)
    result["returncode"] = completed.returncode
    result["stdout"] = completed.stdout[-4000:]
    result["stderr"] = completed.stderr[-4000:]
    result["ocr_performed"] = completed.returncode == 0 and output_path.exists()
    if completed.returncode != 0:
        result["warnings"].append("ocr_failed")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run OCRmyPDF on a scanned PDF.")
    parser.add_argument("input_pdf", help="Input PDF path")
    parser.add_argument("output_pdf", help="Output searchable PDF path")
    parser.add_argument("--language", default="chi_sim+eng")
    parser.add_argument("--no-deskew", action="store_true")
    parser.add_argument("--no-rotate-pages", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        ocr_pdf(
            args.input_pdf,
            args.output_pdf,
            language=args.language,
            deskew=not args.no_deskew,
            rotate_pages=not args.no_rotate_pages,
        ),
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
