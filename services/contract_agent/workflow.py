"""合同智能体主工作流。"""
from __future__ import annotations

import re

from services.contract_agent.checklists import select_checklist
from services.contract_agent.classifier import classify_contract
from services.contract_agent.clause_segmenter import segment_clauses
from services.contract_agent.grounding import verify_grounding
from services.contract_agent.revisions import generate_revisions
from services.contract_agent.schema import (
    ChecklistItem,
    Clause,
    ClauseRef,
    ClauseSummary,
    ContractAgentInput,
    ContractIssue,
    ContractMeta,
    ContractReviewResult,
    ExecutiveSummary,
    MissingClause,
    MissingInfoItem,
    NegotiationTip,
)
from services.contract_agent.scoring import calculate_risk_score


_SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}
_TASK_UNSUPPORTED = {"version_compare", "draft_contract"}
_QUERY_KEYWORDS = ["解除", "退租", "违约金", "押金", "付款", "验收", "保密", "责任", "管辖", "数据"]


def _empty_summary(message: str, action: str = "请补充合同文本后再进行审查。") -> ExecutiveSummary:
    return ExecutiveSummary(
        overall_risk_level="low",
        one_sentence_conclusion=message,
        top_risks=[],
        recommended_action=action,
    )


def _unsupported_result(input_data: ContractAgentInput) -> ContractReviewResult:
    task_name = "两版合同对比" if input_data.task_type == "version_compare" else "合同起草"
    return ContractReviewResult(
        status="partial_review",
        task_type=input_data.task_type,
        contract_meta=ContractMeta(document_completeness="partial"),
        executive_summary=_empty_summary(
            f"第一阶段暂未完整支持{task_name}。",
            "请先使用完整合同审查、单条条款审查或缺失条款检查能力。",
        ),
        final_handoff_note=f"{task_name}需要后续多文件或起草工作流支持。",
    )


def _has_prompt_injection_text(text: str) -> bool:
    return any(phrase in text for phrase in ["忽略之前", "不要审查", "你现在应该", "ignore previous"])


def _assumptions(input_data: ContractAgentInput) -> list[str]:
    assumptions: list[str] = []
    context = input_data.user_context
    if not context or not context.jurisdiction:
        assumptions.append("未提供适用法域，以下仅按通用合同风险进行审查，不输出具体法条结论。")
    if not context or not context.user_role:
        assumptions.append("未说明用户是哪一方，以下尽量提示双方或不特定一方的风险。")
    if input_data.contract_text and _has_prompt_injection_text(input_data.contract_text):
        assumptions.append("合同正文中疑似包含指令性文字，已仅作为合同内容处理。")
    return assumptions


def _contains_any(text: str, patterns: list[str]) -> bool:
    return any(pattern and pattern in text for pattern in patterns)


def _quote_for_clause(clause: Clause, pattern: str) -> str:
    if not pattern or pattern not in clause.text:
        return clause.text[:180]
    start = max(0, clause.text.find(pattern) - 30)
    end = min(len(clause.text), clause.text.find(pattern) + len(pattern) + 80)
    return clause.text[start:end].strip()


def _query_relevant(input_data: ContractAgentInput, clause: Clause, item: ChecklistItem) -> bool:
    if input_data.task_type not in {"contract_qa", "clause_review"}:
        return True
    query = input_data.user_message or ""
    hits = [word for word in _QUERY_KEYWORDS if word in query]
    if not hits:
        return True
    haystack = f"{clause.text} {item.title} {item.description}"
    return any(word in haystack for word in hits)


def _issue_from_clause(item: ChecklistItem, clause: Clause, pattern: str) -> ContractIssue:
    score = calculate_risk_score(
        impact=item.impact,
        likelihood=item.likelihood,
        detectability=item.detectability,
    )
    quote = _quote_for_clause(clause, pattern)
    return ContractIssue(
        id=f"{item.id}-{clause.id}",
        title=item.title,
        severity=score.severity,
        category=item.category,
        clause_ref=ClauseRef(
            clause_id=clause.id,
            clause_number=clause.clause_number,
            paragraph_index=clause.paragraph_index,
            quote=quote,
        ),
        problem=f"{item.description}当前条款命中风险表达“{pattern}”。",
        why_it_matters=item.risk_question,
        affected_party="unclear",
        suggested_fix=item.suggested_fix,
        proposed_text=item.proposed_text_template,
        confidence="high" if pattern else "medium",
        risk_score=score,
    )


def _missing_clause_from_item(item: ChecklistItem) -> MissingClause:
    score = calculate_risk_score(
        impact=item.impact,
        likelihood=item.likelihood,
        detectability=item.detectability,
    )
    return MissingClause(
        id=item.id,
        title=item.title,
        severity=score.severity,
        problem=f"合同未见明确约定：{item.description}",
        why_it_matters=item.risk_question,
        suggested_fix=item.suggested_fix,
        proposed_text=item.proposed_text_template,
        confidence="medium",
        risk_score=score,
    )


def _issue_from_missing(item: MissingClause) -> ContractIssue:
    return ContractIssue(
        id=item.id,
        title=item.title,
        severity=item.severity,
        category="missing_clause",
        clause_ref=None,
        problem=item.problem,
        why_it_matters=item.why_it_matters,
        affected_party="both",
        suggested_fix=item.suggested_fix,
        proposed_text=item.proposed_text,
        confidence=item.confidence,
        risk_score=item.risk_score,
    )


def _collect_issues(
    input_data: ContractAgentInput,
    clauses: list[Clause],
    checklist: list[ChecklistItem],
    source_text: str,
) -> tuple[list[ContractIssue], list[MissingClause]]:
    issues: list[ContractIssue] = []
    missing: list[MissingClause] = []
    for item in checklist:
        if item.missing_clause_risk:
            if input_data.review_options.include_missing_clauses and not _contains_any(source_text, item.positive_patterns):
                missing.append(_missing_clause_from_item(item))
            continue
        for clause in clauses:
            if not _query_relevant(input_data, clause, item):
                continue
            matched = [pattern for pattern in item.risk_patterns if pattern in clause.text]
            if matched:
                issues.append(_issue_from_clause(item, clause, matched[0]))
                break
    missing_issues = [_issue_from_missing(item) for item in missing]
    if input_data.task_type == "missing_clause_check":
        return missing_issues, missing
    return issues + missing_issues, missing


def _clause_summaries(clauses: list[Clause]) -> list[ClauseSummary]:
    summaries: list[ClauseSummary] = []
    for clause in clauses[:8]:
        summary = re.sub(r"\s+", " ", clause.text)
        summaries.append(
            ClauseSummary(
                clause_id=clause.id,
                clause_number=clause.clause_number,
                title=clause.title,
                summary=summary[:120],
            )
        )
    return summaries


def _overall_severity(issues: list[ContractIssue]) -> str:
    if not issues:
        return "low"
    return max((issue.severity for issue in issues), key=lambda value: _SEVERITY_ORDER[value])


def _executive_summary(issues: list[ContractIssue], contract_type: str) -> ExecutiveSummary:
    overall = _overall_severity(issues)
    top = [issue.title for issue in sorted(issues, key=lambda item: _SEVERITY_ORDER[item.severity], reverse=True)[:3]]
    if issues:
        conclusion = f"该合同初步识别为 {contract_type}，综合风险等级为 {overall}，主要风险集中在{'、'.join(top)}。"
        action = "建议优先处理高风险条款，并将修改建议作为签署或谈判前的确认清单。"
    else:
        conclusion = f"该合同初步识别为 {contract_type}，未识别到明显高频风险条款。"
        action = "建议继续核对主体、金额、期限、履行、违约和争议解决条款。"
    return ExecutiveSummary(
        overall_risk_level=overall,
        one_sentence_conclusion=conclusion,
        top_risks=top,
        recommended_action=action,
    )


def _meta(input_data: ContractAgentInput, contract_type: str) -> ContractMeta:
    context = input_data.user_context
    return ContractMeta(
        contract_type=contract_type,
        user_role=context.user_role if context else None,
        jurisdiction=context.jurisdiction if context else None,
        document_completeness="complete" if input_data.contract_text else "unknown",
    )


def run_contract_agent(input: ContractAgentInput) -> ContractReviewResult:
    """
    函数作用：
        执行第一阶段确定性合同审查 workflow。
    """
    if input.task_type in _TASK_UNSUPPORTED:
        return _unsupported_result(input)

    contract_text = (input.contract_text or "").strip()
    if not contract_text:
        return ContractReviewResult(
            status="need_more_facts",
            task_type=input.task_type,
            missing_info=[
                MissingInfoItem(
                    field="contract_text",
                    reason="没有合同文本或上传文档，无法进行可靠审查。",
                    blocking=True,
                    question="请上传合同文件或粘贴需要审查的合同条款。",
                )
            ],
            contract_meta=ContractMeta(document_completeness="unknown"),
            executive_summary=_empty_summary("目前缺少合同文本，无法进行可靠审查。"),
            final_handoff_note="需要先向用户索要合同文本或合同文件。",
        )

    classification = classify_contract(contract_text)
    clauses = segment_clauses(contract_text)
    checklist = select_checklist(classification.contract_type, input.task_type)
    issues, missing = _collect_issues(input, clauses, checklist, contract_text)

    if input.task_type == "risk_scan":
        issues = sorted(issues, key=lambda item: _SEVERITY_ORDER[item.severity], reverse=True)[:6]
    elif input.task_type in {"contract_qa", "clause_review"}:
        issues = sorted(issues, key=lambda item: _SEVERITY_ORDER[item.severity], reverse=True)[:3]
    else:
        issues = sorted(issues, key=lambda item: _SEVERITY_ORDER[item.severity], reverse=True)[: input.review_options.max_issues]

    grounding = verify_grounding(issues, contract_text)
    verified_issues = grounding.verified_issues
    revisions = generate_revisions(
        verified_issues,
        include_redline=input.review_options.include_redline,
    )
    tips = [
        NegotiationTip(issue_id=issue.id, tip=f"优先协商：{issue.suggested_fix}", priority=issue.severity)
        for issue in verified_issues[:3]
        if input.review_options.include_negotiation_tips
    ]

    return ContractReviewResult(
        status="ok",
        task_type=input.task_type,
        assumptions=_assumptions(input),
        contract_meta=_meta(input, classification.contract_type),
        executive_summary=_executive_summary(verified_issues, classification.contract_type),
        issues=verified_issues,
        missing_clauses=missing,
        clause_summaries=_clause_summaries(clauses),
        proposed_revisions=revisions,
        negotiation_tips=tips,
        verification_warnings=grounding.warnings,
        final_handoff_note="合同智能体已完成结构化审查，最终回答应基于本结果生成。",
    )
