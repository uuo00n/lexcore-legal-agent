"""诉讼时效计算器 MCP 工具 —— 根据案由和事件日期计算时效截止日。"""
from __future__ import annotations

import json
from datetime import date

from mcp_server.server import mcp
from mcp_server.knowledge.limitations_table import (
    DEFAULT_RULE,
    LIMITATION_RULES,
    SUSPENSION_WARNING,
)


def _add_years(d: date, years: float) -> date:
    """
    函数作用：
        日期加年数（支持 0.5 年 = 6 个月）。
    输入参数：
        - d: date
        - years: float
    输出参数：
        - date
    """
    if years == int(years):
        y = int(years)
        try:
            return date(d.year + y, d.month, d.day)
        except ValueError:
            # 2月29日 + N年 → 取2月28日
            return date(d.year + y, d.month, d.day - 1)
    else:
        # 非整数年：转为月数
        months = int(years * 12)
        new_month = d.month + months
        new_year = d.year + (new_month - 1) // 12
        new_month = (new_month - 1) % 12 + 1
        try:
            return date(new_year, new_month, d.day)
        except ValueError:
            return date(new_year, new_month, 28)


@mcp.tool()
def statute_of_limitations(event_date: str, case_type: str) -> str:
    """
    函数作用：
        计算诉讼时效截止日期。
    输入参数：
        - event_date: str
        - case_type: str
    输出参数：
        - str
    """
    rule = LIMITATION_RULES.get(case_type)
    if not rule:
        # 尝试模糊匹配
        for key, r in LIMITATION_RULES.items():
            if key in case_type or case_type in key:
                rule = r
                break
    if not rule:
        rule = DEFAULT_RULE

    try:
        start = date.fromisoformat(event_date)
    except ValueError:
        return json.dumps(
            {"error": f"日期格式错误: {event_date}，请使用 YYYY-MM-DD 格式"},
            ensure_ascii=False,
        )

    deadline = _add_years(start, rule.period_years)
    today = date.today()
    remaining_days = (deadline - today).days

    warnings = [SUSPENSION_WARNING]
    if 0 <= remaining_days < 30:
        warnings.append("⚠️ 距离诉讼时效届满不足 30 天，请尽快采取法律行动！")
    if remaining_days < 0:
        warnings.append("⚠️ 诉讼时效可能已届满，但如存在中止/中断事由，时效可能延长。建议尽快咨询律师。")

    supported_types = list(LIMITATION_RULES.keys())

    result = {
        "case_type": rule.case_type,
        "event_date": event_date,
        "period": f"{rule.period_years} 年" if rule.period_years >= 1 else f"{int(rule.period_years * 12)} 个月",
        "deadline": deadline.isoformat(),
        "remaining_days": remaining_days,
        "is_expired": remaining_days < 0,
        "legal_basis": f"{rule.legal_basis} {rule.article}",
        "notes": rule.notes,
        "warnings": warnings,
        "supported_case_types": supported_types,
    }
    return json.dumps(result, ensure_ascii=False)
