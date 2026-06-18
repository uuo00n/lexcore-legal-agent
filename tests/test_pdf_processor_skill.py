from __future__ import annotations

from pathlib import Path

from services.openviking_ingest import build_legal_skill_specs
from services.viking_context import retrieve_viking_context


SKILL_DIR = Path("agent/skills/pdf-processor")


def test_pdf_processor_skill_bundle_exists():
    skill_md = SKILL_DIR / "SKILL.md"
    inspect_script = SKILL_DIR / "scripts" / "inspect_pdf.py"
    extract_script = SKILL_DIR / "scripts" / "extract_pdf_text.py"
    ocr_script = SKILL_DIR / "scripts" / "ocr_pdf.py"

    assert skill_md.exists()
    assert inspect_script.exists()
    assert extract_script.exists()
    assert ocr_script.exists()

    content = skill_md.read_text(encoding="utf-8")
    assert "name: pdf-processor" in content
    assert "OCR" in content
    assert "ocrmypdf" in content
    assert "page-level" in content


def test_pdf_processor_skill_is_retrievable_from_local_context_layer():
    result = retrieve_viking_context(
        "用户上传了扫描版合同 PDF，无法复制文字，需要 OCR 后按页审查。",
        thread_id="pdf-skill-test",
        skill_limit=4,
    )

    skill_uris = [hit.uri for hit in result.hits if hit.context_type == "skill"]
    assert "viking://skills/legal/pdf_processor/" in skill_uris
    assert "PDF 文档处理" in result.prompt
    assert "OCR" in result.prompt


def test_pdf_processor_skill_is_imported_to_openviking_specs():
    specs = build_legal_skill_specs()
    pdf_specs = [item for item in specs if item["name"] == "pdf-processor"]

    assert len(pdf_specs) == 1
    spec = pdf_specs[0]
    assert "pdf" in spec["tags"]
    assert "ocr" in spec["tags"]
    assert "page-level" in spec["content"]
