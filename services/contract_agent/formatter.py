"""合同智能体结果格式化。"""
from __future__ import annotations

from services.contract_agent.schema import ContractReviewResult


def _severity_label(severity: str) -> str:
    return {
        "low": "低",
        "medium": "中",
        "high": "高",
        "critical": "重大",
    }.get(severity, severity)


def render_markdown_report(filename: str, result: ContractReviewResult, *, focus: str = "") -> str:
    """
    函数作用：
        将结构化合同审查结果渲染为 Markdown 报告。
    """
    lines = [
        "# 合同审查报告",
        "",
        f"- 文件名：{filename}",
        f"- 审查重点：{focus or '全面审查'}",
        f"- 审查状态：{result.status}",
        f"- 合同类型：{result.contract_meta.contract_type}",
        f"- 综合风险等级：{_severity_label(result.executive_summary.overall_risk_level)}",
        "",
        "## 一、总体结论",
        "",
        result.executive_summary.one_sentence_conclusion,
        "",
        f"建议动作：{result.executive_summary.recommended_action}",
    ]
    if result.assumptions:
        lines.extend(["", "## 二、审查假设", ""])
        lines.extend(f"- {item}" for item in result.assumptions)

    lines.extend(["", "## 三、风险问题"])
    if not result.issues:
        lines.extend(["", "未识别到明显高频风险条款。"])
    for index, issue in enumerate(result.issues, start=1):
        lines.extend([
            "",
            f"### {index}. {issue.title}",
            "",
            f"- 风险等级：{_severity_label(issue.severity)}",
            f"- 风险分类：{issue.category}",
            f"- 风险评分：impact={issue.risk_score.impact}, likelihood={issue.risk_score.likelihood}, detectability={issue.risk_score.detectability}, total={issue.risk_score.total}",
            f"- 问题说明：{issue.problem}",
            f"- 影响说明：{issue.why_it_matters}",
            f"- 修改建议：{issue.suggested_fix}",
        ])
        if issue.clause_ref and issue.clause_ref.quote:
            ref = issue.clause_ref.clause_number or issue.clause_ref.clause_id or "相关条款"
            lines.extend(["", f"原文依据（{ref}）：", "", f"> {issue.clause_ref.quote}"])
        elif issue.category == "missing_clause":
            lines.append("- 原文依据：合同未见明确约定。")
        if issue.proposed_text:
            lines.extend(["", "建议文本：", "", f"> {issue.proposed_text}"])

    if result.missing_clauses:
        lines.extend(["", "## 四、缺失条款"])
        for item in result.missing_clauses:
            lines.extend([
                "",
                f"- {item.title}：{item.problem}",
                f"  - 建议：{item.suggested_fix}",
            ])

    if result.proposed_revisions:
        lines.extend(["", "## 五、修改建议汇总"])
        for revision in result.proposed_revisions:
            lines.extend([
                "",
                f"### {revision.issue_id}",
                "",
                f"- 修改原因：{revision.reason}",
                f"- 谈判提示：{revision.negotiation_note or '建议结合交易目标协商。'}",
                "",
                "建议文本：",
                "",
                f"> {revision.new_text}",
            ])

    if result.negotiation_tips:
        lines.extend(["", "## 六、谈判建议"])
        lines.extend(f"- {_severity_label(item.priority)}优先级：{item.tip}" for item in result.negotiation_tips)

    if result.verification_warnings:
        lines.extend(["", "## 七、证据校验提示"])
        lines.extend(f"- {item}" for item in result.verification_warnings)

    lines.extend([
        "",
        "## 八、提示",
        "",
        "本报告基于合同文本结构化分析生成，用于辅助审查，不等同于律师正式法律意见。",
        "未提供明确适用法域或可靠检索依据时，本报告不输出具体法条结论。",
    ])
    return "\n".join(lines)


def render_chat_summary(result: ContractReviewResult, *, report_id: str, download_url: str) -> str:
    """
    函数作用：
        生成合同智能体交给主控的用户可读摘要。
    """
    top = result.executive_summary.top_risks[:3]
    top_text = "、".join(top) if top else "未识别到明显高频风险"
    return (
        "我已完成结构化合同审查。\n\n"
        f"- 报告 ID：{report_id}\n"
        f"- 下载地址：{download_url}\n"
        f"- 合同类型：{result.contract_meta.contract_type}\n"
        f"- 综合风险等级：{_severity_label(result.executive_summary.overall_risk_level)}\n"
        f"- 重点风险：{top_text}\n\n"
        f"{result.executive_summary.recommended_action}"
    )
