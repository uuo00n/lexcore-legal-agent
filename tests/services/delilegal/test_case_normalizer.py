import json
from pathlib import Path

from services.delilegal.normalizers import normalize_case_response
from services.delilegal.processors import compress_case_content


FIXTURE = Path(__file__).parents[2] / "fixtures" / "delilegal" / "case_search_response.json"


def test_case_response_is_normalized():
    response = normalize_case_response(json.loads(FIXTURE.read_text(encoding="utf-8")))

    assert response.query_id == "case-query-001"
    assert response.total_count == 1
    item = response.items[0]
    assert item.title == "张某与某公司劳动争议案"
    assert item.cause == "劳动争议"
    assert item.court == "某市中级人民法院"
    assert item.case_number == "（2024）示例民终1号"
    assert item.judgement_date == "2024-05-20"
    assert "上班途中" in item.content


def test_case_processor_returns_sections_not_full_judgment():
    item = normalize_case_response(json.loads(FIXTURE.read_text(encoding="utf-8"))).items[0]
    compact = compress_case_content(item, "上班途中 工伤")

    assert compact["basic_facts"]
    assert compact["dispute_focus"]
    assert compact["court_reasoning"]
    assert compact["judgment_result"]
    assert "content" not in compact
