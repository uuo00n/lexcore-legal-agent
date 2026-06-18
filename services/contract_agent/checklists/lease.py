"""租赁合同审查清单。"""
from __future__ import annotations

from services.contract_agent.schema import ChecklistItem


LEASE_CHECKLIST = [
    ChecklistItem(
        id="LEASE-DEPOSIT-001",
        contract_types=["lease"],
        category="payment",
        title="押金退还条件对承租方不利",
        description="检查押金退还条件、扣除范围和返还期限是否明确合理。",
        risk_question="合同是否约定押金不予退还或扣除条件过宽？",
        severity_default="high",
        risk_patterns=["押金不予退还", "没收押金", "押金不退"],
        suggested_fix="建议明确押金仅能因实际损失、欠费或约定违约情形扣除，并约定返还期限。",
        proposed_text_template="租赁期满且承租方结清费用、返还房屋后，出租方应在七日内无息退还剩余押金；如需扣除，应提供实际损失凭证。",
        impact=4,
        likelihood=4,
        detectability=3,
    ),
    ChecklistItem(
        id="LEASE-REPAIR-001",
        contract_types=["lease"],
        category="liability",
        title="维修责任过度转嫁",
        description="检查房屋自然损耗、主体结构和设施维修责任是否合理分配。",
        risk_question="合同是否把任何损坏和全部维修费用都转给承租方？",
        severity_default="medium",
        risk_patterns=["任何损坏均由承租方", "全部费用", "所有维修均由承租方"],
        suggested_fix="建议区分自然损耗、出租方维修义务和承租方人为损坏责任。",
        proposed_text_template="因自然损耗或房屋主体结构、原有设施质量问题产生的维修由出租方承担；因承租方不当使用造成的损坏由承租方承担。",
        impact=3,
        likelihood=4,
        detectability=3,
    ),
    ChecklistItem(
        id="LEASE-TERM-001",
        contract_types=["lease"],
        category="termination",
        title="提前退租违约责任过重",
        description="检查提前退租责任是否与损失相匹配。",
        risk_question="提前退租是否要求支付剩余租期全部租金或高额违约金？",
        severity_default="high",
        risk_patterns=["剩余租期全部租金", "提前退租需支付", "提前退租", "全部租金作为违约金"],
        suggested_fix="建议设置合理通知期和违约金上限，避免剩余租期全部租金一概承担。",
        proposed_text_template="承租方提前退租的，应提前三十日通知出租方，并承担不超过一个月租金的违约金；出租方应合理减损。",
        impact=4,
        likelihood=3,
        detectability=4,
    ),
]
