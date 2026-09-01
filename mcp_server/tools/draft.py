"""文书模板生成 MCP 工具 —— 根据文书类型和关键事实生成法律文书草稿。"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from mcp_server.server import mcp
from services.rag.retriever import get_retriever

TEMPLATES_DIR = Path(__file__).parent.parent / "knowledge" / "templates"

REQUIRED_FIELDS = {
    "起诉状": ["plaintiff_name", "defendant_name", "claims", "facts", "court_name"],
    "劳动仲裁申请书": ["applicant_name", "respondent_name", "claims", "facts"],
    "合同": ["party_a", "party_b", "subject_matter"],
}


@mcp.tool()
def legal_document_draft(doc_type: str, key_facts: dict) -> str:
    """
    函数作用：
        根据文书类型和关键事实生成法律文书草稿。
    输入参数：
        - doc_type: str
        - key_facts: dict
    输出参数：
        - str
    """
    template_file = TEMPLATES_DIR / f"{doc_type}.txt"
    if not template_file.exists():
        supported = [f.stem for f in TEMPLATES_DIR.glob("*.txt")]
        return json.dumps(
            {"error": f"不支持的文书类型: {doc_type}", "supported_types": supported},
            ensure_ascii=False,
        )

    # 检查必填字段
    required = REQUIRED_FIELDS.get(doc_type, [])
    missing = [f for f in required if f not in key_facts or not key_facts[f]]
    if missing:
        return json.dumps(
            {
                "error": f"缺少必填字段: {missing}",
                "required_fields": required,
                "hint": "请提供以上必填字段后重试",
            },
            ensure_ascii=False,
        )

    # 检索相关法条作为法律依据
    facts_text = key_facts.get("facts", "") or key_facts.get("subject_matter", "")
    if facts_text:
        retriever = get_retriever()
        chunks = retriever.retrieve(facts_text, top_k=5)
        legal_basis = "\n".join(
            f"- 《{c.law_name}》{c.article_no}：{c.content[:80]}" for c in chunks
        )
    else:
        legal_basis = "（请补充事实描述以自动检索法律依据）"

    # 填充模板
    key_facts.setdefault("legal_basis", legal_basis)
    key_facts.setdefault("date", date.today().strftime("%Y年%m月%d日"))
    key_facts.setdefault("evidence", "（请补充证据清单）")

    template = template_file.read_text(encoding="utf-8")

    # 逐字段替换（容忍缺失字段，保留占位符）
    for k, v in key_facts.items():
        template = template.replace(f"{{{k}}}", str(v))

    return template
