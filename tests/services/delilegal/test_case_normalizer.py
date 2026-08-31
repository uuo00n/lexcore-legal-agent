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
    assert item.case_date == "2024-05-20"
    assert item.summary is None
    assert item.judgment == item.content
    assert item.source == "delilegal"
    assert item.score is None
    assert "上班途中" in item.content


def test_case_processor_returns_sections_not_full_judgment():
    item = normalize_case_response(json.loads(FIXTURE.read_text(encoding="utf-8"))).items[0]
    compact = compress_case_content(item, "上班途中 工伤")

    assert compact["basic_facts"]
    assert compact["dispute_focus"]
    assert compact["court_reasoning"]
    assert compact["judgment_result"]
    assert "content" not in compact


def test_case_response_adapts_standard_field_aliases():
    response = normalize_case_response(
        {
            "data": {
                "list": [
                    {
                        "id": "case-002",
                        "title": "示例案件",
                        "court": "示例法院",
                        "caseNumber": "（2025）示例1号",
                        "caseDate": "2025-03-01",
                        "cause": "劳动争议",
                        "summary": "劳动合同解除争议。",
                        "judgment": "本院认为：应支付补偿。\n判决如下：支付经济补偿。",
                        "source": "得理法律数据库",
                        "similarity": 0.88,
                    }
                ]
            }
        }
    )

    item = response.items[0]
    assert item.case_date == "2025-03-01"
    assert item.summary == "劳动合同解除争议。"
    assert item.judgment == item.content
    assert item.source == "得理法律数据库"
    assert item.score == 0.88
