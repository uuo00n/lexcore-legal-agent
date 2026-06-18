---
name: pdf-processor
description: Use when a legal consultation, contract review, evidence review, or filing task involves PDF files, scanned PDFs, image-only documents, OCR, unreadable uploads, page-level extraction, or questions about why a PDF has no text.
---

# PDF Processor

## Purpose

Use this skill when the legal agent needs to turn a PDF into reliable text before legal analysis. The skill handles ordinary text-layer PDFs, detects scanned/image-only PDFs, and uses OCR when the local runtime has `ocrmypdf` installed.

The skill is part of the legal agent's document ingestion workflow. It should feed contract review, evidence review, lawsuit filing, and general legal consultation with page-level text and clear processing status.

## When to Trigger

Trigger for user requests such as:

- "帮我看这个 PDF / 合同 PDF / 扫描合同"
- "PDF 读不出来 / 上传后没有文字"
- "扫描件 / 图片 PDF / 盖章合同 / 法院材料"
- "OCR / 识别图片里的字"
- "按页提取 / 找第几页 / 页码引用"

Do not use this skill for DOCX or plain TXT files unless the user is comparing them with PDF content.

## Workflow

1. Inspect the PDF first.
   - Run `scripts/inspect_pdf.py`.
   - Check `has_text_layer`, `scanned_like`, page count, and per-page text counts.
2. If the PDF has a text layer, extract page-level text.
   - Run `scripts/extract_pdf_text.py`.
   - Preserve page numbers for later citation or contract clause location.
3. If the PDF is scanned-like, try OCR only when available.
   - Run `scripts/ocr_pdf.py`.
   - If `ocrmypdf` is missing, report that OCR is required and unavailable locally.
4. After OCR succeeds, extract text again from the OCR output PDF.
5. Never claim a scanned PDF was read if OCR was not performed or extraction returned empty text.

## Script Reference

All scripts accept local filesystem paths and write JSON to stdout unless otherwise noted.

```bash
python agent/skills/pdf-processor/scripts/inspect_pdf.py input.pdf
python agent/skills/pdf-processor/scripts/extract_pdf_text.py input.pdf
python agent/skills/pdf-processor/scripts/ocr_pdf.py input.pdf output.ocr.pdf
```

## Output Expectations

When this skill feeds legal analysis, produce or store:

- `source_path`
- `page_count`
- `has_text_layer`
- `scanned_like`
- `ocr_performed`
- `pages`: page-level text with 1-based page numbers
- `warnings`: missing OCR, encrypted PDF, empty pages, truncated text

## Failure Rules

- Encrypted PDFs: say the file is encrypted if pypdf cannot read it.
- Empty extraction: treat as scanned-like unless page count is zero.
- Missing OCR dependency: say `ocrmypdf` is not installed and OCR cannot run locally.
- Legal citations: page text can support document facts, but legal authorities still must come from the legal search tools.
