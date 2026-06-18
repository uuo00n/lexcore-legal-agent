from __future__ import annotations

from services.legal_analysis import (
    analyze_legal_message,
    build_follow_up_response,
    check_fact_completeness,
    classify_legal_intent,
    score_legal_answer,
    should_ask_follow_up,
    validate_citations,
)


def test_classify_legal_intent_detects_labor_dispute():
    result = classify_legal_intent("公司拖欠工资，我想申请劳动仲裁")

    assert result["is_legal"] is True
    assert result["category"] == "labor"
    assert "工资" in result["matched_keywords"]


def test_fact_completeness_returns_missing_dimensions():
    result = check_fact_completeness("房东不退押金")

    assert result["category"] == "lease"
    assert result["is_sufficient"] is False
    assert "租赁合同" in result["missing_dimensions"]


def test_validate_citations_splits_verified_and_unsupported():
    answer = "依据《民法典》第五百七十七条处理，不能直接引用《刑法》第二百六十四条。"
    retrieved = [{"law_name": "民法典", "article_no": "第五百七十七条"}]

    result = validate_citations(answer, retrieved)

    assert result["total"] == 2
    assert result["verified"] == [{"law_name": "民法典", "article_no": "第五百七十七条"}]
    assert result["unsupported"] == [{"law_name": "刑法", "article_no": "第二百六十四条"}]


def test_analyze_legal_message_includes_evidence_and_risk():
    result = analyze_legal_message("对方欠款不还，我准备起诉", "建议保留转账记录。", [])

    assert result["intent"]["category"] == "debt"
    assert result["risk"]["level"] == "medium"
    assert "借条或欠条" in result["evidence_checklist"]


def test_should_ask_follow_up_for_sparse_legal_question():
    decision = should_ask_follow_up("房东不退押金")
    response = build_follow_up_response("房东不退押金")

    assert decision["should_ask"] is True
    assert "请补充" in response


def test_drug_plant_threshold_question_is_legal_info_not_follow_up():
    decision = should_ask_follow_up("种植罂粟几株犯法")
    intent = classify_legal_intent("种植罂粟几株犯法")

    assert intent["is_legal"] is True
    assert intent["category"] == "criminal"
    assert decision["should_ask"] is False
    assert decision["reason"] == "legal_information_query"


def test_follow_up_acknowledgement_does_not_create_contract_fact_gap():
    decision = should_ask_follow_up("我只是想了解一下这个法律信息。")

    assert decision["should_ask"] is False
    assert decision["reason"] == "legal_information_query"


def test_procedure_question_is_legal_info_not_follow_up():
    decision = should_ask_follow_up("劳动仲裁应该去哪里申请？")

    assert decision["should_ask"] is False
    assert decision["reason"] == "legal_information_query"


def test_score_legal_answer_checks_structure():
    score = score_legal_answer(
        "公司辞退我怎么办",
        "根据你描述，可能存在违法解除风险。建议准备劳动合同并申请劳动仲裁。",
        [],
    )

    assert score["score"] >= 60
    assert score["checks"]["has_action_advice"] is True
