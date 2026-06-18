"""法律场景确定性分析工具。

这些函数不替代 LLM，而是提供可测试、可追踪的轻量判断：
识别法律意图、检查事实缺口、校验引用、给出风险等级和证据清单。
"""
from __future__ import annotations

import re
from typing import Any


LEGAL_KEYWORDS = {
    "labor": ["劳动", "工资", "加班", "辞退", "工伤", "社保", "劳动合同", "离职"],
    "lease": ["租房", "房租", "押金", "房东", "租赁", "退租"],
    "debt": ["借钱", "欠钱", "欠款", "借条", "还款", "债务", "转账", "催收"],
    "injury": ["受伤", "打人", "殴打", "校园霸凌", "侵权", "赔偿", "医疗费"],
    "contract": ["合同", "违约", "定金", "协议", "付款", "交付"],
    "marriage": ["离婚", "抚养", "彩礼", "夫妻", "财产分割", "婚姻"],
    "criminal": ["刑法", "犯罪", "犯法", "违法", "拘留", "判刑", "毒品", "罂粟", "大麻", "非法种植"],
}

FACT_DIMENSIONS = {
    "labor": {
        "劳动关系": ["劳动合同", "入职", "公司", "用人单位"],
        "时间": ["多久", "什么时候", "日期", "年月", "天"],
        "金额": ["工资", "金额", "元", "赔偿", "补偿"],
        "证据": ["证据", "聊天记录", "工资条", "考勤", "录音"],
    },
    "lease": {
        "租赁合同": ["租赁合同", "合同", "约定"],
        "押金/租金": ["押金", "租金", "金额", "元"],
        "退租原因": ["退租", "到期", "违约", "损坏"],
        "证据": ["证据", "照片", "聊天记录", "转账"],
    },
    "debt": {
        "借款关系": ["借条", "借款", "转账", "欠条"],
        "金额": ["金额", "元", "万"],
        "期限": ["还款", "期限", "什么时候", "日期"],
        "证据": ["证据", "聊天记录", "转账", "录音"],
    },
    "injury": {
        "主体身份": ["年龄", "学生", "成年人", "学校", "对方"],
        "伤害后果": ["受伤", "伤情", "医疗", "鉴定", "住院"],
        "行为经过": ["经过", "打", "推", "骂", "威胁"],
        "证据": ["证据", "监控", "聊天记录", "报警", "医院"],
    },
    "contract": {
        "合同内容": ["合同", "协议", "约定", "条款"],
        "履行情况": ["付款", "交付", "履行", "完成"],
        "违约事实": ["违约", "逾期", "拒绝", "解除"],
        "证据": ["证据", "聊天记录", "发票", "转账"],
    },
    "marriage": {
        "婚姻状态": ["结婚", "离婚", "登记", "分居"],
        "子女/财产": ["孩子", "抚养", "房产", "存款", "彩礼"],
        "争议目标": ["想要", "要求", "分割", "抚养权"],
        "证据": ["证据", "流水", "聊天记录", "协议"],
    },
    "criminal": {
        "行为类型": ["种植", "持有", "买卖", "运输", "携带", "吸食", "注射"],
        "数量": ["几", "多少", "株", "克", "数量"],
        "对象": ["罂粟", "大麻", "毒品", "种子", "幼苗"],
        "处理状态": ["已经", "打算", "铲除", "收获", "自首"],
    },
}

_LAW_CITATION_RE = re.compile(
    r"《([^》]+)》\s*(第[一二三四五六七八九十百千万亿零〇两\d]+条(?:之[一二三四五六七八九十百千万亿零〇两\d]+)?)"
)

LEGAL_INFORMATION_PATTERNS = (
    "几株", "多少", "几克", "标准", "条件", "构成", "算不算", "是否", "会不会",
    "犯不犯法", "犯法", "违法吗", "违法", "法律信息", "了解一下", "只是想了解",
    "法条", "规定", "怎么规定", "量刑", "处罚", "判几年", "去哪里", "哪里申请",
    "怎么申请", "流程", "期限", "材料", "需要什么",
)


def classify_legal_intent(text: str) -> dict[str, Any]:
    """
    函数作用：
        判断用户输入是否属于法律问题，并给出粗分类。
    输入参数：
        - text: str
    输出参数：
        - dict[str, Any]
    """
    normalized = text.strip()
    scores = {
        category: sum(1 for keyword in keywords if keyword in normalized)
        for category, keywords in LEGAL_KEYWORDS.items()
    }
    category, score = max(scores.items(), key=lambda item: item[1])
    generic_hits = any(word in normalized for word in ["法律", "起诉", "报警", "赔偿", "责任", "法院", "律师"])
    is_legal = score > 0 or generic_hits
    return {
        "is_legal": is_legal,
        "category": category if is_legal and score > 0 else "general",
        "confidence": min(1.0, 0.35 + score * 0.2 + (0.2 if generic_hits else 0.0)) if is_legal else 0.0,
        "matched_keywords": [
            keyword
            for keywords in LEGAL_KEYWORDS.values()
            for keyword in keywords
            if keyword in normalized
        ],
    }


def is_legal_information_query(text: str) -> bool:
    """
    函数作用：
        判断用户是否在询问法律规则、门槛或概念本身，而不是要求个案结论。
    输入参数：
        - text: str
    输出参数：
        - bool
    """
    normalized = text.strip()
    if not normalized:
        return False
    return any(pattern in normalized for pattern in LEGAL_INFORMATION_PATTERNS)


def _dimension_has_signal(category: str, dimension: str, keywords: list[str], text: str) -> bool:
    if any(keyword in text for keyword in keywords):
        return True
    if category == "labor" and dimension == "时间":
        return bool(re.search(r"(工作|入职|在职|干了|做了|服务)?[一二三四五六七八九十两\d]+\s*(年|个月|月)", text))
    return False


def check_fact_completeness(text: str) -> dict[str, Any]:
    """
    函数作用：
        检查常见法律场景的事实维度是否充分。
    输入参数：
        - text: str
    输出参数：
        - dict[str, Any]
    """
    intent = classify_legal_intent(text)
    category = intent["category"]
    dimensions = FACT_DIMENSIONS.get(category, FACT_DIMENSIONS["contract"])
    missing = [
        name for name, keywords in dimensions.items()
        if not _dimension_has_signal(category, name, keywords, text)
    ]
    return {
        "category": category,
        "is_sufficient": len(missing) <= 1 if intent["is_legal"] else True,
        "missing_dimensions": missing,
        "follow_up_questions": [f"请补充{name}相关事实。" for name in missing[:3]],
    }


def _citation_key(law_name: str, article_no: str) -> tuple[str, str]:
    """
    函数作用：
        生成法条引用比对 key。
    输入参数：
        - law_name: str
        - article_no: str
    输出参数：
        - tuple[str, str]
    """
    law = re.sub(r"\s+", "", law_name).replace("中华人民共和国", "")
    article = re.sub(r"\s+", "", article_no)
    return law, article


def validate_citations(answer: str, retrieved_laws: list[dict[str, Any]]) -> dict[str, Any]:
    """
    函数作用：
        校验回答中的明确法条引用是否来自本轮检索结果。
    输入参数：
        - answer: str
        - retrieved_laws: list[dict[str, Any]]
    输出参数：
        - dict[str, Any]
    """
    cited = [
        {"law_name": match.group(1), "article_no": match.group(2)}
        for match in _LAW_CITATION_RE.finditer(answer or "")
    ]
    allowed = {
        _citation_key(item.get("law_name", ""), item.get("article_no", ""))
        for item in retrieved_laws
        if item.get("law_name") and item.get("article_no")
    }
    verified = []
    unsupported = []
    for item in cited:
        if _citation_key(item["law_name"], item["article_no"]) in allowed:
            verified.append(item)
        else:
            unsupported.append(item)
    return {
        "total": len(cited),
        "verified": verified,
        "unsupported": unsupported,
        "is_fully_supported": not unsupported,
    }


def assess_risk_level(text: str) -> dict[str, Any]:
    """
    函数作用：
        基于关键词给出粗粒度风险等级，供 dashboard 和追问策略参考。
    输入参数：
        - text: str
    输出参数：
        - dict[str, Any]
    """
    high_hits = [word for word in ["刑事", "拘留", "逮捕", "重伤", "起诉期限", "强制执行"] if word in text]
    medium_hits = [word for word in ["起诉", "仲裁", "赔偿", "解除合同", "辞退", "欠款"] if word in text]
    if high_hits:
        return {"level": "high", "reasons": high_hits}
    if medium_hits:
        return {"level": "medium", "reasons": medium_hits}
    return {"level": "low", "reasons": []}


def build_evidence_checklist(text: str) -> list[str]:
    """
    函数作用：
        根据法律场景生成证据准备清单。
    输入参数：
        - text: str
    输出参数：
        - list[str]
    """
    category = classify_legal_intent(text)["category"]
    common = ["双方身份信息", "沟通记录", "付款或履行凭证"]
    scenario = {
        "labor": ["劳动合同或入职证明", "工资流水", "考勤记录", "解除/辞退通知"],
        "lease": ["租赁合同", "押金和租金转账记录", "房屋交接照片", "维修或损坏证据"],
        "debt": ["借条或欠条", "转账记录", "催款记录", "还款期限约定"],
        "injury": ["报警回执", "医院诊断记录", "伤情照片", "监控或证人线索"],
        "contract": ["合同原件", "发票或收据", "交付记录", "违约通知"],
        "marriage": ["结婚登记信息", "财产凭证", "子女抚养相关材料", "协商记录"],
    }
    return scenario.get(category, common) + common


def analyze_legal_message(text: str, answer: str = "", retrieved_laws: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    函数作用：
        汇总法律意图、事实完整性、风险、证据和引用校验结果。
    输入参数：
        - text: str
        - answer: str，默认值 ''
        - retrieved_laws: list[dict[str, Any]] | None，默认值 None
    输出参数：
        - dict[str, Any]
    """
    retrieved_laws = retrieved_laws or []
    return {
        "intent": classify_legal_intent(text),
        "facts": check_fact_completeness(text),
        "risk": assess_risk_level(text),
        "evidence_checklist": build_evidence_checklist(text),
        "citations": validate_citations(answer, retrieved_laws),
        "answer_score": score_legal_answer(text, answer, retrieved_laws),
    }


def should_ask_follow_up(text: str, *, has_uploaded_doc: bool = False) -> dict[str, Any]:
    """
    函数作用：
        判断是否应在进入 RAG/工具调用前先追问事实。
    输入参数：
        - text: str
        - has_uploaded_doc: bool，默认值 False
    输出参数：
        - dict[str, Any]
    """
    intent = classify_legal_intent(text)
    facts = check_fact_completeness(text)
    # 上传了文档时，文档可能已经包含关键事实，避免过早拦截。
    if has_uploaded_doc or not intent["is_legal"]:
        return {"should_ask": False, "questions": [], "reason": "not_required", "facts": facts}
    # 用户只是在问法律规则/处罚门槛时，事实缺口不应阻断检索和回答。
    if is_legal_information_query(text):
        return {"should_ask": False, "questions": [], "reason": "legal_information_query", "facts": facts}
    # 短问题且缺失多项关键事实时，先追问比直接检索更稳。
    should_ask = len(text.strip()) < 80 and len(facts["missing_dimensions"]) >= 3
    return {
        "should_ask": should_ask,
        "questions": facts["follow_up_questions"],
        "reason": "missing_core_facts" if should_ask else "enough_for_initial_answer",
        "facts": facts,
    }


def build_follow_up_response(text: str) -> str:
    """
    函数作用：
        构造事实不足时的追问回复。
    输入参数：
        - text: str
    输出参数：
        - str
    """
    decision = should_ask_follow_up(text)
    questions = decision["questions"][:3]
    if not questions:
        return ""
    lines = "\n".join(f"{i}. {question}" for i, question in enumerate(questions, start=1))
    return (
        "这个问题需要先补充几个关键事实，我再帮你判断法律风险和处理路径：\n\n"
        f"{lines}"
    )


def score_legal_answer(
    question: str,
    answer: str,
    retrieved_laws: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    函数作用：
        对法律回答做结构化质量评分。
    输入参数：
        - question: str
        - answer: str
        - retrieved_laws: list[dict[str, Any]] | None，默认值 None
    输出参数：
        - dict[str, Any]
    """
    retrieved_laws = retrieved_laws or []
    citations = validate_citations(answer, retrieved_laws)
    intent = classify_legal_intent(question)
    checks = {
        "has_fact_analysis": any(word in answer for word in ["事实", "需要确认", "如果", "根据你描述"]),
        "has_citation": citations["total"] > 0 or bool(retrieved_laws),
        "citations_supported": citations["is_fully_supported"],
        "has_risk_notice": any(word in answer for word in ["风险", "可能", "不能保证", "建议咨询律师"]),
        "has_action_advice": any(word in answer for word in ["建议", "可以", "准备", "申请", "起诉", "投诉", "报警"]),
        "not_overpromising": not any(word in answer for word in ["一定胜诉", "必然", "肯定能赢", "百分百"]),
    }
    if not intent["is_legal"]:
        checks["has_citation"] = True
        checks["citations_supported"] = True
    score = round(sum(1 for passed in checks.values() if passed) / len(checks) * 100)
    return {
        "score": score,
        "checks": checks,
        "citations": citations,
    }
