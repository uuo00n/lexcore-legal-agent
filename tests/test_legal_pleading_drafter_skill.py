from __future__ import annotations

from pathlib import Path

from services.openviking_ingest import build_legal_skill_specs
from services.viking_context import retrieve_viking_context


SKILL_DIR = Path("agent/skills/legal-pleading-drafter")


def test_legal_pleading_drafter_skill_bundle_exists():
    skill_md = SKILL_DIR / "SKILL.md"
    document_types = SKILL_DIR / "references" / "document-types.md"
    fact_checklist = SKILL_DIR / "references" / "fact-checklist.md"
    pleading_style = SKILL_DIR / "references" / "pleading-style.md"
    quality_review = SKILL_DIR / "references" / "quality-review.md"
    punctuation_script = SKILL_DIR / "scripts" / "fix_punctuation.py"

    assert skill_md.exists()
    assert document_types.exists()
    assert fact_checklist.exists()
    assert pleading_style.exists()
    assert quality_review.exists()
    assert punctuation_script.exists()

    content = skill_md.read_text(encoding="utf-8")
    assert "name: legal-pleading-drafter" in content
    assert "起诉状" in content
    assert "答辩状" in content
    assert "去 AI 腔" in content
    assert "不得虚构" in content


def test_legal_pleading_drafter_is_retrievable_from_local_context_layer():
    result = retrieve_viking_context(
        "我要写一份民事起诉状，帮我整理诉讼请求、事实与理由、证据目录，并且不要有 AI 腔。",
        thread_id="legal-pleading-skill-test",
        skill_limit=5,
    )

    skill_uris = [hit.uri for hit in result.hits if hit.context_type == "skill"]
    assert "viking://skills/legal/legal_pleading_drafter/" in skill_uris
    assert "法律文书生成" in result.prompt
    assert "不得虚构事实" in result.prompt


def test_legal_pleading_drafter_is_imported_to_openviking_specs():
    specs = build_legal_skill_specs()
    pleading_specs = [item for item in specs if item["name"] == "legal-pleading-drafter"]

    assert len(pleading_specs) == 1
    spec = pleading_specs[0]
    assert "pleading" in spec["tags"]
    assert "litigation" in spec["tags"]
    assert "起诉状" in spec["content"]
    assert "去 AI 腔" in spec["content"]
