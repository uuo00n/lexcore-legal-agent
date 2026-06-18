"""合同审查报告生成服务。"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from services.contract_agent.formatter import render_markdown_report
from services.contract_agent.schema import ContractAgentInput, ContractReviewResult
from services.contract_agent.workflow import run_contract_agent
from services.legal_analysis import build_evidence_checklist


REPORT_DIR = Path("data/reports")


def _run_review(contract_text: str, focus: str = "") -> ContractReviewResult:
    """
    函数作用：
        运行结构化合同智能体。
    输入参数：
        - contract_text: str
        - focus: str，默认值 ''
    输出参数：
        - ContractReviewResult
    """
    task_type = "risk_scan" if any(word in focus for word in ["快速", "扫描", "风险扫描"]) else "contract_review"
    return run_contract_agent(
        ContractAgentInput(
            user_message=focus or "全面审查合同",
            task_type=task_type,
            contract_text=contract_text,
        )
    )


def _finding_from_issue(issue) -> dict[str, Any]:
    return {
        "risk": issue.title,
        "severity": issue.severity,
        "category": issue.category,
        "clause": issue.clause_ref.quote if issue.clause_ref and issue.clause_ref.quote else issue.problem,
        "suggestion": issue.suggested_fix,
        "problem": issue.problem,
        "why_it_matters": issue.why_it_matters,
        "risk_score": issue.risk_score.model_dump(mode="json"),
    }


def analyze_contract_text(contract_text: str, focus: str = "") -> dict[str, Any]:
    """
    函数作用：
        生成合同审查结构化结果。
    输入参数：
        - contract_text: str
        - focus: str，默认值 ''
    输出参数：
        - dict[str, Any]
    """
    result = _run_review(contract_text, focus)
    findings = [_finding_from_issue(issue) for issue in result.issues]
    if not findings:
        findings = [{
            "risk": "未识别到明显高频风险词",
            "severity": "low",
            "category": "other",
            "clause": contract_text[:300],
            "suggestion": "建议继续核对主体信息、标的、价款、履行期限、违约责任和争议解决条款。",
            "risk_score": {"impact": 1, "likelihood": 1, "detectability": 1, "total": 1.0, "severity": "low"},
        }]
    return {
        "focus": focus or "全面审查",
        "risk_level": result.executive_summary.overall_risk_level,
        "risk_reasons": result.executive_summary.top_risks,
        "findings": findings[:12],
        "evidence_checklist": build_evidence_checklist("合同 " + contract_text[:200]),
        "char_count": len(contract_text),
        "contract_result": result.model_dump(mode="json"),
    }


def render_contract_report(filename: str, contract_text: str, focus: str = "") -> str:
    """
    函数作用：
        渲染 Markdown 合同审查报告。
    输入参数：
        - filename: str
        - contract_text: str
        - focus: str，默认值 ''
    输出参数：
        - str
    """
    result = _run_review(contract_text, focus)
    return render_markdown_report(filename, result, focus=focus)


def save_contract_report(filename: str, contract_text: str, focus: str = "") -> dict[str, str]:
    """
    函数作用：
        生成并保存合同审查报告。
    输入参数：
        - filename: str
        - contract_text: str
        - focus: str，默认值 ''
    输出参数：
        - dict[str, str]
    """
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_id = f"contract-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    result = _run_review(contract_text, focus)
    markdown = render_markdown_report(filename, result, focus=focus)
    path = REPORT_DIR / f"{report_id}.md"
    path.write_text(markdown, encoding="utf-8")
    return {
        "report_id": report_id,
        "path": str(path),
        "markdown": markdown,
        "contract_result": result.model_dump(mode="json"),
    }
