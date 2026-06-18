from __future__ import annotations

from services.case_retrieval import format_cases_for_prompt, search_similar_cases


def test_search_similar_cases_returns_lease_deposit_case():
    cases = search_similar_cases("租房到期后房东不退押金")

    assert cases
    assert cases[0]["category"] == "lease"
    assert "押金" in cases[0]["title"]


def test_format_cases_for_prompt_marks_reference_not_case_law():
    cases = search_similar_cases("朋友欠款不还，只有聊天记录")
    prompt = format_cases_for_prompt(cases)

    assert "相似法律场景参考" in prompt
    assert "不是正式判例引用" in prompt
