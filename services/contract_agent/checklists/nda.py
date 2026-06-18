"""NDA 审查清单。"""
from __future__ import annotations

from services.contract_agent.schema import ChecklistItem


NDA_CHECKLIST = [
    ChecklistItem(
        id="NDA-SCOPE-001",
        contract_types=["nda"],
        category="confidentiality",
        title="保密信息范围过宽",
        description="检查保密信息定义是否过宽，是否缺少标记、范围和例外。",
        risk_question="保密信息是否覆盖所有信息且不要求明确标记？",
        severity_default="high",
        risk_patterns=["所有信息", "任何方式获知", "不论是否标记", "无论是否标明"],
        suggested_fix="建议限定保密信息范围，并增加已公开、独立开发、合法取得等例外。",
        proposed_text_template="保密信息应限于披露方以书面、口头或其他方式明确标识为保密，或根据披露情境合理应被理解为保密的信息。",
        impact=4,
        likelihood=4,
        detectability=4,
    ),
    ChecklistItem(
        id="NDA-TERM-001",
        contract_types=["nda"],
        category="confidentiality",
        title="保密期限过长",
        description="检查保密期限是否合理，是否区分一般商业信息和商业秘密。",
        risk_question="合同是否约定永久或明显过长的保密期限？",
        severity_default="medium",
        risk_patterns=["永久", "长期", "十年", "10年", "20年"],
        suggested_fix="建议一般保密信息设置固定期限，商业秘密可持续至其不再构成商业秘密。",
        proposed_text_template="除依法构成商业秘密的信息外，接收方的保密义务自披露之日起持续三年。",
        impact=3,
        likelihood=4,
        detectability=3,
    ),
    ChecklistItem(
        id="NDA-LIABILITY-001",
        contract_types=["nda"],
        category="breach",
        title="保密违约责任过重",
        description="检查违约金是否过高、是否不区分违约程度。",
        risk_question="保密违约责任是否约定固定高额违约金或任何违约均触发？",
        severity_default="high",
        risk_patterns=["50万元", "高额违约金", "任何违约均应支付", "全部损失"],
        suggested_fix="建议区分违约程度，违约金与实际损失及过错程度匹配。",
        proposed_text_template="违约方应赔偿守约方因此遭受的实际、直接损失；固定违约金应与违约程度和实际损失相匹配。",
        impact=5,
        likelihood=3,
        detectability=4,
    ),
]
