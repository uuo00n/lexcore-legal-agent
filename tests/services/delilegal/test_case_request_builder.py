from services.delilegal.enums import CourtLevel, JudgementType
from services.delilegal.normalizers import build_case_request, build_law_request
from services.delilegal.schemas import CaseSearchInput, LawSearchInput


def test_case_request_contains_enums_filters_and_omits_none():
    value = CaseSearchInput(
        page_no=2,
        page_size=5,
        sort_field="time",
        sort_order="asc",
        case_year_start="2020-08-05",
        case_year_end="2023-08-13",
        court_levels=[CourtLevel.SUPREME, CourtLevel.INTERMEDIATE],
        judgement_types=[JudgementType.JUDGMENT, JudgementType.RULING],
        keywords=["上班途中", "交通事故", "工伤"],
    )

    payload = build_case_request(value)

    assert payload["pageNo"] == 2
    assert payload["sortField"] == "time"
    assert payload["sortOrder"] == "asc"
    assert payload["condition"]["courtLevelArr"] == ["0", "2"]
    assert payload["condition"]["caseYearStart"] == "2020-08-05"
    assert payload["condition"]["caseYearEnd"] == "2023-08-13"
    assert payload["condition"]["judgementTypeArr"] == ["30", "31"]
    assert payload["condition"]["keywordArr"] == ["上班途中", "交通事故", "工伤"]
    assert "longText" not in payload["condition"]


def test_long_text_takes_priority_and_none_fields_are_absent():
    value = CaseSearchInput(
        keywords=["不应发送"],
        long_text="劳动者上班途中发生交通事故",
    )

    payload = build_case_request(value)

    assert payload["condition"] == {"longText": "劳动者上班途中发生交通事故"}
    assert payload["pageNo"] == 1
    assert payload["pageSize"] == 5
    assert payload["sortField"] == "correlation"
    assert payload["sortOrder"] == "desc"


def test_law_request_uses_confirmed_keyword_condition_shape():
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
        "condition": {"keywordArr": ["劳动合同法 经济补偿"]},
    }
