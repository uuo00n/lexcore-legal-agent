#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pypdf import PdfReader


def inspect_pdf(path: str | Path, *, min_chars_per_page: int = 20) -> dict[str, Any]:
    pdf_path = Path(path)
    result: dict[str, Any] = {
        "source_path": str(pdf_path),
        "exists": pdf_path.exists(),
        "page_count": 0,
        "encrypted": False,
        "has_text_layer": False,
        "scanned_like": False,
        "total_text_chars": 0,
        "pages": [],
        "warnings": [],
    }
    if not pdf_path.exists():
        result["warnings"].append("file_not_found")
        return result

    try:
        reader = PdfReader(str(pdf_path))
        result["encrypted"] = bool(reader.is_encrypted)
        result["page_count"] = len(reader.pages)
        page_infos = []
        total = 0
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # pragma: no cover - pypdf parser variations
                text = ""
                result["warnings"].append(f"page_{index}_extract_error:{exc}")
            char_count = len(text.strip())
            total += char_count
            page_infos.append({"page": index, "text_chars": char_count})
        result["pages"] = page_infos
        result["total_text_chars"] = total
        result["has_text_layer"] = any(item["text_chars"] >= min_chars_per_page for item in page_infos)
        result["scanned_like"] = result["page_count"] > 0 and not result["has_text_layer"]
        if result["scanned_like"]:
            result["warnings"].append("pdf_has_little_or_no_extractable_text")
    except Exception as exc:
        result["warnings"].append(f"pdf_read_error:{exc}")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect PDF text-layer availability.")
    parser.add_argument("pdf", help="Input PDF path")
    parser.add_argument("--min-chars-per-page", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(inspect_pdf(args.pdf, min_chars_per_page=args.min_chars_per_page), ensure_ascii=False))


if __name__ == "__main__":
    main()
