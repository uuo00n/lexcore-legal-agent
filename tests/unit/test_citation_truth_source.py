"""P0-1 唯一引用真相源的回归用例（§三十 用例 5、6、10）。

覆盖三件事：
1. 别名法规名必须经 canonical 映射通过核验，而不是被判为编造引用；
2. 编造引用必须被确定性核验拦下，并给出可供 Repair Router 路由的结构化类型；
3. ``verified_evidence`` → ``verification_result`` → 最终 citations → 顶层
   ``legal_analysis.citations`` 全链路口径一致。
"""
from __future__ import annotations

from agent.evidence import (
    assign_ref_ids,
    evidence_payload,
    normalize_case_evidence,
    normalize_law_evidence,
)
from agent.nodes.citation_verifier import verify_citations
from agent.nodes.verifier import verify_plan_results
from services.legal_analysis import validate_citations

# 上游返回的法规名带版本括注，报告里通常写简称。
RAW_LAW = {
    "law_name": "中华人民共和国劳动合同法(2012修正)",
    "article_no": "第八十五条",
    "content": "用人单位未按照劳动合同约定支付劳动报酬的，由劳动行政部门责令限期支付。",
    "source_type": "delilegal_law",
    "source_id": "labor-contract-law-85",
    "timeliness_name": "现行有效",
}
RAW_CASE = {
    "case_name": "张某诉某公司劳动争议案",
    "case_number": "（2023）京02民终888号",
    "case_id": "case-888",
    "source_type": "delilegal_case",
    "basic_facts": "公司拖欠工资，法院判决支付欠付工资及赔偿金。",
}


def _law_evidence() -> dict:
    law = normalize_law_evidence(RAW_LAW)
    assert law is not None
    return assign_ref_ids([evidence_payload(law)], "law")[0]


def _case_evidence() -> dict:
    case = normalize_case_evidence(RAW_CASE)
    assert case is not None
    return assign_ref_ids([evidence_payload(case)], "case")[0]


def _state(analysis: str, sources: list[dict]) -> dict:
    report = {
        "report_id": "step_1:legal_reasoning_agent",
        "task_id": "step_1",
        "agent_name": "legal_reasoning_agent",
        "summary": "用人单位应当支付欠付工资",
        "findings": {"analysis": analysis},
        "sources": sources,
    }
    return {
        "plan": [
            {
                "step_id": "step_1",
                "task_type": "legal_consultation",
                "assigned_agent": "legal_reasoning_agent",
                "required": True,
                "status": "completed",
                "result": report,
            }
        ],
        "agent_reports": [report],
        "retrieved_laws": [_law_evidence()],
        "retrieved_cases": [_case_evidence()],
    }


def test_alias_law_name_is_verified_through_canonical_mapping():
    """§三十 用例 5：《劳动合同法》第八十五条 == 证据「劳动合同法(2012修正) 第八十五条」。"""
    law = _law_evidence()
    state = _state("依据《劳动合同法》第八十五条，公司应当限期支付欠付工资。", [law])

    evidence, issues = verify_citations(state)

    assert issues == []
    assert evidence["citation_verified"] == evidence["citation_total"] >= 1
    assert evidence["citation_unsupported"] == 0
    # 引用去重按证据身份，不按引用写法：两种写法只产出一条引用。
    law_citations = [item for item in evidence["citations"] if item["kind"] == "law"]
    assert len(law_citations) == 1
    assert law_citations[0]["evidence_id"] == law["evidence_id"]
    assert law_citations[0]["ref_id"] == "law_001"


def test_real_case_name_with_fabricated_case_no_is_rejected():
    """§三十 用例 6：案号是权威标识，真名 + 假案号仍必须判为编造引用。

    这里的 source 连 ``ref_id`` / ``evidence_id`` 都是真的，只把案号改成编造的——
    Agent 内部按 ``case_001`` 引用，但用户看到的是写出的案号，两者矛盾时不能算已核验。
    """
    case = _case_evidence()
    state = _state(
        "参考张某诉某公司劳动争议案（2099）京02民终1号的处理思路。",
        [{**case, "case_no": "（2099）京02民终1号"}],
    )

    result = verify_plan_results(state)

    assert result["passed"] is False
    assert any("（2099）京02民终1号" in item for item in result["invalid_citations"])
    assert "citation_invalid" in {issue["type"] for issue in result["structured_issues"]}
    assert all(
        issue["severity"] == "blocking" and issue["source"] == "deterministic"
        for issue in result["structured_issues"]
        if issue["type"] == "citation_invalid"
    )


def test_verified_evidence_is_the_single_citation_truth_source():
    """§三十 用例 10：核验统计、最终引用与顶层引用校验必须口径一致。"""
    from agent.nodes.answer import _final_citations, verified_evidence_of

    law = _law_evidence()
    answer = "依据《劳动合同法》第八十五条，公司应当限期支付欠付工资，逾期需加付赔偿金。"
    state = _state(answer, [law])

    result = verify_plan_results(state)
    evidence = verify_citations(state)[0]
    state["verified_evidence"] = evidence
    state["verification_result"] = result

    assert result["citation_report"] == {
        "citation_total": evidence["citation_total"],
        "citation_verified": evidence["citation_verified"],
        "citation_unsupported": evidence["citation_unsupported"],
    }
    # Answer Generator 复用 State 中的真相源，而不是自己重扫报告。
    assert verified_evidence_of(state) is evidence
    final = _final_citations(evidence)
    assert [item["evidence_id"] for item in final] == [
        item["evidence_id"] for item in evidence["citations"]
    ]
    assert all(item["citation_id"] and "url" in item for item in final)
    # 顶层 legal_analysis 用同一份已核验法条判断引用支持度。
    top_level = validate_citations(answer, evidence["laws"])
    assert top_level["is_fully_supported"] is True
    assert top_level["unsupported"] == []


def test_top_level_citation_check_rejects_laws_outside_verified_evidence():
    evidence = verify_citations(_state("依据《劳动合同法》第八十五条处理。", [_law_evidence()]))[0]

    top_level = validate_citations(
        "依据《劳动合同法》第八十五条，并参考《中华人民共和国民法典》第五百七十七条。",
        evidence["laws"],
    )

    assert top_level["total"] == 2
    assert top_level["verified"] == [{"law_name": "劳动合同法", "article_no": "第八十五条"}]
    assert top_level["unsupported"] == [
        {"law_name": "中华人民共和国民法典", "article_no": "第五百七十七条"}
    ]
