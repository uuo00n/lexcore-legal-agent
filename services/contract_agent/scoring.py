"""合同风险评分。"""
from __future__ import annotations

from services.contract_agent.schema import RiskScore, Severity


def _clamp(value: int) -> int:
    return max(1, min(5, int(value)))


def severity_from_total(total: float) -> Severity:
    if total >= 4.5:
        return "critical"
    if total >= 3.5:
        return "high"
    if total >= 2.3:
        return "medium"
    return "low"


def calculate_risk_score(*, impact: int, likelihood: int, detectability: int) -> RiskScore:
    """
    函数作用：
        根据影响、概率和不易发现程度计算风险等级。
    """
    impact = _clamp(impact)
    likelihood = _clamp(likelihood)
    detectability = _clamp(detectability)
    total = round(impact * 0.5 + likelihood * 0.3 + detectability * 0.2, 2)
    return RiskScore(
        impact=impact,
        likelihood=likelihood,
        detectability=detectability,
        total=total,
        severity=severity_from_total(total),
    )
