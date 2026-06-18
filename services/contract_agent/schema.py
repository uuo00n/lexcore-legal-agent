"""合同智能体结构化数据模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ContractTaskType = Literal[
    "contract_review",
    "clause_review",
    "contract_summary",
    "risk_scan",
    "redline_suggestion",
    "contract_qa",
    "version_compare",
    "draft_contract",
    "missing_clause_check",
]
ContractStatus = Literal["ok", "need_more_facts", "cannot_review", "partial_review"]
ContractType = Literal[
    "nda",
    "employment",
    "labor",
    "lease",
    "sales",
    "service",
    "saas",
    "software_development",
    "loan",
    "equity_investment",
    "partnership",
    "agency",
    "distribution",
    "ip_license",
    "data_processing",
    "construction",
    "settlement",
    "unknown",
]
Severity = Literal["low", "medium", "high", "critical"]
IssueCategory = Literal[
    "payment",
    "liability",
    "termination",
    "breach",
    "confidentiality",
    "ip",
    "data_privacy",
    "non_compete",
    "dispute_resolution",
    "jurisdiction",
    "delivery_acceptance",
    "renewal",
    "assignment",
    "force_majeure",
    "missing_clause",
    "ambiguity",
    "inconsistency",
    "other",
]
AffectedParty = Literal["user", "counterparty", "both", "unclear"]
Confidence = Literal["low", "medium", "high"]


class ContractUserContext(BaseModel):
    """用户侧合同审查上下文。"""

    user_role: str | None = None
    jurisdiction: str | None = None
    business_goal: str | None = None
    risk_tolerance: Literal["low", "medium", "high"] | None = None
    preferred_language: Literal["zh", "en"] = "zh"


class ContractReviewOptions(BaseModel):
    """合同审查选项。"""

    depth: Literal["quick", "standard", "deep"] = "standard"
    include_redline: bool = True
    include_negotiation_tips: bool = True
    include_missing_clauses: bool = True
    include_legal_basis: bool = False
    max_issues: int = 12


class ContractAgentInput(BaseModel):
    """合同智能体输入。"""

    user_message: str
    task_type: ContractTaskType = "contract_review"
    contract_text: str | None = None
    user_context: ContractUserContext | None = None
    review_options: ContractReviewOptions = Field(default_factory=ContractReviewOptions)


class Clause(BaseModel):
    """合同条款片段。"""

    id: str
    clause_number: str | None = None
    title: str | None = None
    text: str
    paragraph_index: int
    start_offset: int
    end_offset: int
    parent_clause_id: str | None = None


class ClauseRef(BaseModel):
    """风险问题对应的原文定位。"""

    clause_id: str | None = None
    clause_number: str | None = None
    page: int | None = None
    paragraph_index: int | None = None
    quote: str | None = None


class RiskScore(BaseModel):
    """风险评分。"""

    impact: int
    likelihood: int
    detectability: int
    total: float
    severity: Severity


class MissingInfoItem(BaseModel):
    """缺失信息项。"""

    field: str
    reason: str
    blocking: bool = False
    question: str | None = None


class ContractMeta(BaseModel):
    """合同元信息。"""

    contract_type: ContractType = "unknown"
    parties: list[str] = Field(default_factory=list)
    user_role: str | None = None
    jurisdiction: str | None = None
    language: str = "zh"
    effective_date: str | None = None
    term: str | None = None
    total_amount: str | None = None
    document_completeness: Literal["complete", "partial", "unknown"] = "unknown"


class ExecutiveSummary(BaseModel):
    """合同审查总体结论。"""

    overall_risk_level: Severity = "low"
    one_sentence_conclusion: str
    top_risks: list[str] = Field(default_factory=list)
    recommended_action: str


class ContractIssue(BaseModel):
    """合同风险问题。"""

    id: str
    title: str
    severity: Severity
    category: IssueCategory
    clause_ref: ClauseRef | None = None
    problem: str
    why_it_matters: str
    affected_party: AffectedParty = "unclear"
    suggested_fix: str
    proposed_text: str | None = None
    confidence: Confidence = "medium"
    risk_score: RiskScore


class MissingClause(BaseModel):
    """缺失条款。"""

    id: str
    title: str
    severity: Severity
    category: Literal["missing_clause"] = "missing_clause"
    clause_ref: ClauseRef | None = None
    problem: str
    why_it_matters: str
    suggested_fix: str
    proposed_text: str | None = None
    confidence: Confidence = "medium"
    risk_score: RiskScore


class ClauseSummary(BaseModel):
    """条款摘要。"""

    clause_id: str
    clause_number: str | None = None
    title: str | None = None
    summary: str


class ProposedRevision(BaseModel):
    """建议修改文本。"""

    issue_id: str
    old_text: str | None = None
    new_text: str
    reason: str
    negotiation_note: str | None = None


class NegotiationTip(BaseModel):
    """谈判建议。"""

    issue_id: str | None = None
    tip: str
    priority: Severity = "medium"


class ContractClassification(BaseModel):
    """合同类型识别结果。"""

    contract_type: ContractType
    confidence: float
    matched_signals: list[str] = Field(default_factory=list)


class ChecklistItem(BaseModel):
    """结构化审查清单项。"""

    id: str
    contract_types: list[ContractType]
    category: IssueCategory
    title: str
    description: str
    risk_question: str
    severity_default: Severity
    missing_clause_risk: bool = False
    positive_patterns: list[str] = Field(default_factory=list)
    risk_patterns: list[str] = Field(default_factory=list)
    suggested_fix: str
    proposed_text_template: str | None = None
    roles: list[str] = Field(default_factory=list)
    impact: int = 3
    likelihood: int = 3
    detectability: int = 3


class GroundingVerificationResult(BaseModel):
    """证据校验结果。"""

    verified_issues: list[ContractIssue]
    warnings: list[str] = Field(default_factory=list)
    dropped_issue_ids: list[str] = Field(default_factory=list)


class ContractReviewResult(BaseModel):
    """合同智能体结构化输出。"""

    status: ContractStatus
    task_type: ContractTaskType
    assumptions: list[str] = Field(default_factory=list)
    missing_info: list[MissingInfoItem] = Field(default_factory=list)
    contract_meta: ContractMeta = Field(default_factory=ContractMeta)
    executive_summary: ExecutiveSummary
    issues: list[ContractIssue] = Field(default_factory=list)
    missing_clauses: list[MissingClause] = Field(default_factory=list)
    clause_summaries: list[ClauseSummary] = Field(default_factory=list)
    proposed_revisions: list[ProposedRevision] = Field(default_factory=list)
    negotiation_tips: list[NegotiationTip] = Field(default_factory=list)
    verification_warnings: list[str] = Field(default_factory=list)
    final_handoff_note: str = ""
