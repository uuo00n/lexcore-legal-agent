"""非检索类法律工具的共享 Service Layer。"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from services.limitations_rules import (
    DEFAULT_RULE,
    LIMITATION_RULES,
    SUSPENSION_WARNING,
)
from services.rag.retriever import get_retriever


TEMPLATES_DIR = Path(__file__).parents[1] / "data" / "templates"
REQUIRED_FIELDS = {
    "起诉状": ["plaintiff_name", "defendant_name", "claims", "facts", "court_name"],
    "劳动仲裁申请书": ["applicant_name", "respondent_name", "claims", "facts"],
    "合同": ["party_a", "party_b", "subject_matter"],
}


def law_compare_service(law_a: str, law_b: str, topic: str) -> str:
    """对比两部法律在指定主题下的本地法库条款。"""
    retriever = get_retriever()
    chunks_a = retriever.retrieve(f"{law_a} {topic}", top_k=5)
    chunks_b = retriever.retrieve(f"{law_b} {topic}", top_k=5)
    filtered_a = [chunk for chunk in chunks_a if law_a in chunk.law_name] or chunks_a[:3]
    filtered_b = [chunk for chunk in chunks_b if law_b in chunk.law_name] or chunks_b[:3]
    return json.dumps(
        {
            "topic": topic,
            "law_a": {
                "name": law_a,
                "articles": [
                    {
                        "article_no": chunk.article_no,
                        "hierarchy": chunk.hierarchy,
                        "content": chunk.content,
                    }
                    for chunk in filtered_a
                ],
            },
            "law_b": {
                "name": law_b,
                "articles": [
                    {
                        "article_no": chunk.article_no,
                        "hierarchy": chunk.hierarchy,
                        "content": chunk.content,
                    }
                    for chunk in filtered_b
                ],
            },
        },
        ensure_ascii=False,
    )


def risk_assess_service(facts: str) -> str:
    """检索事实相关法条并生成风险分析提示。"""
    chunks = get_retriever().retrieve(facts, top_k=8)
    return json.dumps(
        {
            "situation": facts,
            "relevant_laws": [
                {
                    "law_name": chunk.law_name,
                    "article_no": chunk.article_no,
                    "hierarchy": chunk.hierarchy,
                    "content": chunk.content,
                }
                for chunk in chunks
            ],
            "analysis_hint": "请基于以上法条分析法律风险点，给出风险等级和应对建议。",
        },
        ensure_ascii=False,
    )


def contract_review_service(contract_text: str, focus_areas: str = "") -> str:
    """检索合同相关法条并生成结构化审查上下文。"""
    query = focus_areas + " " + contract_text[:200] if focus_areas else contract_text[:500]
    chunks = get_retriever().retrieve(query.strip(), top_k=8)
    return json.dumps(
        {
            "contract_text": contract_text[:3000],
            "is_truncated": len(contract_text) > 3000,
            "focus_areas": focus_areas or "全面审查",
            "relevant_laws": [
                {
                    "law_name": chunk.law_name,
                    "article_no": chunk.article_no,
                    "content": chunk.content,
                }
                for chunk in chunks
            ],
            "review_hint": "请基于以上法条指出不合规或明显不利条款，并给出修改建议。",
        },
        ensure_ascii=False,
    )


def _add_years(value: date, years: float) -> date:
    if years == int(years):
        try:
            return date(value.year + int(years), value.month, value.day)
        except ValueError:
            return date(value.year + int(years), value.month, value.day - 1)
    months = int(years * 12)
    new_month = value.month + months
    new_year = value.year + (new_month - 1) // 12
    new_month = (new_month - 1) % 12 + 1
    try:
        return date(new_year, new_month, value.day)
    except ValueError:
        return date(new_year, new_month, 28)


def statute_of_limitations_service(event_date: str, case_type: str) -> str:
    """根据共享规则表计算诉讼时效截止日。"""
    rule = LIMITATION_RULES.get(case_type)
    if not rule:
        rule = next(
            (
                item
                for key, item in LIMITATION_RULES.items()
                if key in case_type or case_type in key
            ),
            DEFAULT_RULE,
        )
    try:
        start = date.fromisoformat(event_date)
    except ValueError:
        return json.dumps(
            {"error": f"日期格式错误: {event_date}，请使用 YYYY-MM-DD 格式"},
            ensure_ascii=False,
        )
    deadline = _add_years(start, rule.period_years)
    remaining_days = (deadline - date.today()).days
    warnings = [SUSPENSION_WARNING]
    if 0 <= remaining_days < 30:
        warnings.append("⚠️ 距离诉讼时效届满不足 30 天，请尽快采取法律行动！")
    if remaining_days < 0:
        warnings.append("⚠️ 诉讼时效可能已届满；如存在中止或中断事由，时效可能延长。")
    return json.dumps(
        {
            "case_type": rule.case_type,
            "event_date": event_date,
            "period": (
                f"{rule.period_years} 年"
                if rule.period_years >= 1
                else f"{int(rule.period_years * 12)} 个月"
            ),
            "deadline": deadline.isoformat(),
            "remaining_days": remaining_days,
            "is_expired": remaining_days < 0,
            "legal_basis": f"{rule.legal_basis} {rule.article}",
            "notes": rule.notes,
            "warnings": warnings,
            "supported_case_types": list(LIMITATION_RULES.keys()),
        },
        ensure_ascii=False,
    )


def legal_document_draft_service(doc_type: str, key_facts: dict[str, Any]) -> str:
    """根据模板和关键事实生成法律文书草稿。"""
    template_file = TEMPLATES_DIR / f"{doc_type}.txt"
    if not template_file.exists():
        supported = [item.stem for item in TEMPLATES_DIR.glob("*.txt")]
        return json.dumps(
            {"error": f"不支持的文书类型: {doc_type}", "supported_types": supported},
            ensure_ascii=False,
        )
    facts = dict(key_facts)
    required = REQUIRED_FIELDS.get(doc_type, [])
    missing = [field for field in required if not facts.get(field)]
    if missing:
        return json.dumps(
            {
                "error": f"缺少必填字段: {missing}",
                "required_fields": required,
                "hint": "请提供以上必填字段后重试",
            },
            ensure_ascii=False,
        )
    facts_text = facts.get("facts", "") or facts.get("subject_matter", "")
    if facts_text:
        chunks = get_retriever().retrieve(facts_text, top_k=5)
        legal_basis = "\n".join(
            f"- 《{chunk.law_name}》{chunk.article_no}：{chunk.content[:80]}"
            for chunk in chunks
        )
    else:
        legal_basis = "（请补充事实描述以自动检索法律依据）"
    facts.setdefault("legal_basis", legal_basis)
    facts.setdefault("date", date.today().strftime("%Y年%m月%d日"))
    facts.setdefault("evidence", "（请补充证据清单）")
    template = template_file.read_text(encoding="utf-8")
    for key, value in facts.items():
        template = template.replace(f"{{{key}}}", str(value))
    return template


__all__ = [
    "contract_review_service",
    "law_compare_service",
    "legal_document_draft_service",
    "risk_assess_service",
    "statute_of_limitations_service",
]
