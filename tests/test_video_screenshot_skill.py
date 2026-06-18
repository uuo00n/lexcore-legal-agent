from __future__ import annotations

from pathlib import Path

from services.openviking_ingest import build_legal_skill_specs
from services.viking_context import retrieve_viking_context


SKILL_DIR = Path("agent/skills/video-screenshot")


def test_video_screenshot_skill_bundle_exists():
    skill_md = SKILL_DIR / "SKILL.md"
    extract_script = SKILL_DIR / "scripts" / "extract.py"
    lib_script = SKILL_DIR / "scripts" / "lib.py"
    setup_ref = SKILL_DIR / "references" / "setup.md"
    strategy_ref = SKILL_DIR / "references" / "strategy-and-params.md"

    assert skill_md.exists()
    assert extract_script.exists()
    assert lib_script.exists()
    assert setup_ref.exists()
    assert strategy_ref.exists()

    content = skill_md.read_text(encoding="utf-8")
    assert "name: video-screenshot" in content
    assert "微信聊天录屏" in content
    assert "SHA256" in content
    assert "_report.json" in content


def test_video_screenshot_skill_is_retrievable_from_local_context_layer():
    result = retrieve_viking_context(
        "我有一段微信聊天录屏，想提取成截图作为借款纠纷证据。",
        thread_id="video-skill-test",
        skill_limit=5,
    )

    skill_uris = [hit.uri for hit in result.hits if hit.context_type == "skill"]
    assert "viking://skills/legal/video_screenshot/" in skill_uris
    assert "视频截图提取" in result.prompt
    assert "证据" in result.prompt


def test_video_screenshot_skill_is_imported_to_openviking_specs():
    specs = build_legal_skill_specs()
    video_specs = [item for item in specs if item["name"] == "video-screenshot"]

    assert len(video_specs) == 1
    spec = video_specs[0]
    assert "video" in spec["tags"]
    assert "evidence" in spec["tags"]
    assert "聊天录屏" in spec["content"]
    assert "SHA256" in spec["content"]
