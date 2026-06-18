"""合同审查 grounding 校验。"""
from __future__ import annotations

from services.contract_agent.schema import ContractIssue, GroundingVerificationResult


def verify_grounding(issues: list[ContractIssue], source_text: str) -> GroundingVerificationResult:
    """
    函数作用：
        校验风险问题是否能回到合同原文，避免把建议或缺失项伪装成原文。
    """
    warnings: list[str] = []
    verified: list[ContractIssue] = []
    source = source_text or ""
    for issue in issues:
        current = issue
        if issue.category == "missing_clause":
            if issue.clause_ref is not None:
                warnings.append(f"{issue.id}: missing_clause must not include a source quote")
                current = issue.model_copy(update={"clause_ref": None})
            verified.append(current)
            continue

        quote = issue.clause_ref.quote if issue.clause_ref else None
        if quote and quote in source:
            verified.append(issue)
            continue

        warnings.append(f"{issue.id}: quote not found in source text")
        verified.append(issue.model_copy(update={"clause_ref": None, "confidence": "low"}))
    return GroundingVerificationResult(verified_issues=verified, warnings=warnings, dropped_issue_ids=[])
