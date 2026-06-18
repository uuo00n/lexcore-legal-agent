from __future__ import annotations

from services.contract_agent.clause_segmenter import segment_clauses
from services.contract_agent.classifier import classify_contract
from services.contract_agent.grounding import verify_grounding
from services.contract_agent.schema import ClauseRef, ContractIssue, RiskScore
from services.contract_agent.scoring import calculate_risk_score


def test_segment_clauses_preserves_clause_numbers_and_source_offsets():
    text = (
        "服务合同\n"
        "第一条 服务内容\n乙方提供软件开发服务。\n"
        "第二条 付款\n甲方应在验收后五日内付款。\n"
    )

    clauses = segment_clauses(text)

    assert [clause.clause_number for clause in clauses[:2]] == ["第一条", "第二条"]
    assert clauses[0].text in text
    assert text[clauses[0].start_offset:clauses[0].end_offset] == clauses[0].text
    assert "验收后五日内付款" in clauses[1].text


def test_classify_contract_uses_contract_type_signals():
    nda = classify_contract("保密协议：披露方提供保密信息，接收方承担保密义务，保密期限十年。")
    lease = classify_contract("房屋租赁合同：出租方收取租金和押金，承租方负责水电费。")
    service = classify_contract("技术服务合同：乙方提交交付成果，甲方按验收标准支付服务费。")

    assert nda.contract_type == "nda"
    assert lease.contract_type == "lease"
    assert service.contract_type == "service"
    assert nda.confidence >= 0.6


def test_calculate_risk_score_maps_total_to_severity():
    critical = calculate_risk_score(impact=5, likelihood=4, detectability=4)
    medium = calculate_risk_score(impact=3, likelihood=2, detectability=2)
    low = calculate_risk_score(impact=1, likelihood=1, detectability=1)

    assert critical.total == 4.5
    assert critical.severity == "critical"
    assert medium.severity == "medium"
    assert low.severity == "low"


def test_verify_grounding_warns_when_quote_is_not_in_source_text():
    score = RiskScore(impact=3, likelihood=3, detectability=3, total=3.0, severity="medium")
    issue = ContractIssue(
        id="ISSUE-1",
        title="付款条件不清",
        severity="medium",
        category="payment",
        clause_ref=ClauseRef(quote="合同约定十日内付款"),
        problem="付款条件缺少触发节点。",
        why_it_matters="付款节点不清容易产生履行争议。",
        affected_party="both",
        suggested_fix="明确付款时间和付款条件。",
        confidence="medium",
        risk_score=score,
    )

    result = verify_grounding([issue], "合同约定验收后五日内付款。")

    assert result.verified_issues[0].confidence == "low"
    assert result.verified_issues[0].clause_ref is None
    assert result.warnings


def test_verify_grounding_removes_fake_quote_from_missing_clause():
    score = RiskScore(impact=3, likelihood=3, detectability=3, total=3.0, severity="medium")
    issue = ContractIssue(
        id="MISS-1",
        title="缺少争议解决条款",
        severity="medium",
        category="missing_clause",
        clause_ref=ClauseRef(quote="由甲方所在地法院管辖"),
        problem="合同未见明确争议解决条款。",
        why_it_matters="发生纠纷时可能增加维权不确定性。",
        affected_party="both",
        suggested_fix="补充适用法律和管辖法院或仲裁机构。",
        confidence="medium",
        risk_score=score,
    )

    result = verify_grounding([issue], "第一条 服务内容\n乙方提供服务。")

    assert result.verified_issues[0].clause_ref is None
    assert "missing_clause" in result.warnings[0]
