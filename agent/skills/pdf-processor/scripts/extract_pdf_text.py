#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pypdf import PdfReader


def extract_pdf_text(path: str | Path, *, max_chars: int = 60000) -> dict[str, Any]:
    pdf_path = Path(path)
    result: dict[str, Any] = {
        "source_path": str(pdf_path),
        "exists": pdf_path.exists(),
        "page_count": 0,
        "total_text_chars": 0,
        "truncated": False,
        "pages": [],
        "warnings": [],
    }
    if not pdf_path.exists():
        result["warnings"].append("file_not_found")
        return result

    try:
        reader = PdfReader(str(pdf_path))
        result["page_count"] = len(reader.pages)
        total = 0
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            remaining = max(0, max_chars - total)
            if len(text) > remaining:
                text = text[:remaining]
                result["truncated"] = True
            total += len(text)
            pages.append({"page": index, "text": text, "text_chars": len(text)})
            if total >= max_chars:
                result["truncated"] = True
                break
        result["pages"] = pages
        result["total_text_chars"] = total
        if total == 0 and result["page_count"] > 0:
            result["warnings"].append("empty_text_extraction")
    except Exception as exc:
        result["warnings"].append(f"pdf_read_error:{exc}")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract page-level text from a PDF.")
    parser.add_argument("pdf", help="Input PDF path")
    parser.add_argument("--max-chars", type=int, default=60000)
    args = parser.parse_args()
    print(json.dumps(extract_pdf_text(args.pdf, max_chars=args.max_chars), ensure_ascii=False))


if __name__ == "__main__":
    main()
