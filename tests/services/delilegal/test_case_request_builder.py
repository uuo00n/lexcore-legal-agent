from services.delilegal.normalizers import build_case_request, build_law_request
from services.delilegal.schemas import CaseSearchInput, LawSearchInput


def test_case_request_uses_official_query_shape():
    value = CaseSearchInput(
        page_no=2,
        page_size=5,
        sort_field="time",
        sort_order="asc",
        keywords=["上班途中", "交通事故", "工伤"],
    )

    payload = build_case_request(value)

    assert payload["pageNo"] == 2
    assert payload["sortField"] == "time"
    assert payload["sortOrder"] == "asc"
    assert payload["query"] == "上班途中 交通事故 工伤"
    assert "condition" not in payload


def test_long_text_takes_priority_and_none_fields_are_absent():
    value = CaseSearchInput(
        keywords=["不应发送"],
        long_text="劳动者上班途中发生交通事故",
    )

    payload = build_case_request(value)

    assert payload["query"] == "劳动者上班途中发生交通事故"
    assert payload["pageNo"] == 1
    assert payload["pageSize"] == 5
    assert payload["sortField"] == "correlation"
    assert payload["sortOrder"] == "desc"


def test_law_request_uses_official_query_shape():
    payload = build_law_request(
        LawSearchInput(
            query="劳动合同法 经济补偿",
            page_no=1,
            page_size=3,
            sort_field="correlation",
            sort_order="desc",
        )
    )

    assert payload == {
        "pageNo": 1,
        "pageSize": 3,
        "sortField": "correlation",
        "sortOrder": "desc",
        "query": "劳动合同法 经济补偿",
    }
