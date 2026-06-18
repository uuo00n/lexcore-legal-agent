"""服务合同审查清单。"""
from __future__ import annotations

from services.contract_agent.schema import ChecklistItem


SERVICE_CHECKLIST = [
    ChecklistItem(
        id="SERVICE-LIABILITY-MISS",
        contract_types=["service", "software_development", "saas", "unknown"],
        category="missing_clause",
        title="缺少责任限制或责任上限条款",
        description="检查合同是否约定赔偿范围、间接损失排除和累计责任上限。",
        risk_question="合同是否未见责任上限或赔偿范围限制？",
        severity_default="medium",
        missing_clause_risk=True,
        positive_patterns=["责任上限", "累计赔偿", "不超过", "间接损失"],
        suggested_fix="建议补充累计赔偿责任上限，并明确间接损失排除。",
        proposed_text_template="除故意或重大过失外，任何一方因本合同承担的累计赔偿责任不超过本合同项下已收取或应支付金额的总额。",
        impact=4,
        likelihood=3,
        detectability=3,
    ),
]
