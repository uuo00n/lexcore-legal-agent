"""合同修改建议生成。"""
from __future__ import annotations

from services.contract_agent.schema import ContractIssue, ProposedRevision


def generate_revisions(issues: list[ContractIssue], *, include_redline: bool = True) -> list[ProposedRevision]:
    """
    函数作用：
        为高风险和关键中风险问题生成建议修改文本。
    """
    if not include_redline:
        return []
    revisions: list[ProposedRevision] = []
    for issue in issues:
        if issue.severity not in {"medium", "high", "critical"} or not issue.proposed_text:
            continue
        revisions.append(
            ProposedRevision(
                issue_id=issue.id,
                old_text=issue.clause_ref.quote if issue.clause_ref else None,
                new_text=issue.proposed_text,
                reason=issue.suggested_fix,
                negotiation_note="建议作为优先谈判点提出，先争取明确边界和责任上限。",
            )
        )
    return revisions
