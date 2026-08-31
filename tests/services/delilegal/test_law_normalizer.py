import json
from pathlib import Path

from services.delilegal.normalizers import normalize_law_response
from services.delilegal.processors import extract_relevant_articles


FIXTURE = Path(__file__).parents[2] / "fixtures" / "delilegal" / "law_search_response.json"


def test_law_response_is_normalized_with_required_metadata():
    response = normalize_law_response(json.loads(FIXTURE.read_text(encoding="utf-8")))

    item = response.items[0]
    assert item.title == "中华人民共和国示例法"
    assert item.law_name == "中华人民共和国示例法"
    assert item.article is None
    assert item.active_date == "2025-02-01"
    assert item.effective_date == "2025-02-01"
    assert item.publish_date == "2025-01-01"
    assert item.publisher_name == "示例机关"
    assert item.timeliness_name == "有效"
    assert item.status == "有效"
    assert item.level_name == "法律"
    assert item.source == "delilegal"
    assert item.score is None
    assert "第二条" in item.content


def test_law_processor_returns_articles_not_full_law():
    item = normalize_law_response(json.loads(FIXTURE.read_text(encoding="utf-8"))).items[0]
    compact = extract_relevant_articles(item, "劳动关系 权益", max_articles=1)

    assert compact["timeliness_name"] == "有效"
    assert len(compact["relevant_articles"]) == 1
    assert "劳动关系" in compact["relevant_articles"][0]["content"]
    assert compact["relevant_articles"][0]["content"] != item.content


def test_law_response_adapts_standard_field_aliases():
    response = normalize_law_response(
        {
            "data": {
                "list": [
                    {
                        "id": "law-002",
                        "title": "示例条例第二条",
                        "lawName": "示例条例",
                        "articleNo": "第二条",
                        "content": "第二条 示例内容。",
                        "publishDate": "2025-01-01",
                        "effectiveDate": "2025-02-01",
                        "status": "有效",
                        "source": "得理法律数据库",
                        "correlation": "0.91",
                    }
                ]
            }
        }
    )

    item = response.items[0]
    assert item.law_name == "示例条例"
    assert item.article == "第二条"
    assert item.effective_date == "2025-02-01"
    assert item.status == "有效"
    assert item.source == "得理法律数据库"
    assert item.score == 0.91
