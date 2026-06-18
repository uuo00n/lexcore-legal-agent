from __future__ import annotations

from pathlib import Path

from eval.context_ab import infer_expected_context
from services.openviking_ingest import build_legal_skill_specs
from services.viking_context import retrieve_viking_context


SKILL_DIR = Path("agent/skills/wage-dispute-workflow")


def test_wage_dispute_skill_bundle_exists():
    skill_md = SKILL_DIR / "SKILL.md"

    assert skill_md.exists()

    content = skill_md.read_text(encoding="utf-8")
    assert "name: wage-dispute-workflow" in content
    assert "工资争议咨询流程" in content
    assert "claim item" in content
    assert "劳动监察" in content
    assert "Do not cite statutes" in content


def test_wage_dispute_skill_is_retrievable_from_local_context_layer():
    result = retrieve_viking_context(
        "公司说工资两个月发一次，还拖欠了我的加班费，这样合法吗？",
        thread_id="wage-skill-test",
        skill_limit=5,
    )

    skill_uris = [hit.uri for hit in result.hits if hit.context_type == "skill"]
    assert "viking://skills/legal/wage_dispute_workflow/" in skill_uris
    assert "工资争议咨询流程" in result.prompt
    assert "劳动监察" in result.prompt


def test_wage_dispute_skill_is_imported_to_openviking_specs():
    specs = build_legal_skill_specs()
    wage_specs = [item for item in specs if item["name"] == "wage-dispute-workflow"]

    assert len(wage_specs) == 1
    spec = wage_specs[0]
    assert "labor" in spec["tags"]
    assert "wage" in spec["tags"]
    assert "payroll" in spec["tags"]
    assert "加班费" in spec["content"]
    assert "不虚构工资" in spec["content"]


def test_wage_dispute_skill_is_expected_for_wage_eval_items():
    expected = infer_expected_context({
        "question": "公司拖欠工资，我可以主动离职并要求补偿吗？",
        "acceptable_contexts": ["劳动合同法_第三十八条"],
    })

    assert "viking://resources/laws/labor/" in expected.resource_uris
    assert "viking://skills/legal/wage_dispute_workflow/" in expected.skill_uris
