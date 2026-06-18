"""类案/场景检索。

第一版使用内置法律场景库，而不是外部裁判文书数据源。它给 Agent 提供
“相似争议如何分析”的结构化参考，避免把类案说成真实判例。
"""
from __future__ import annotations

from typing import Any

from services.legal_analysis import classify_legal_intent


SCENARIO_CASES: list[dict[str, Any]] = [
    {
        "id": "labor-unpaid-wage",
        "category": "labor",
        "title": "拖欠工资与劳动仲裁",
        "keywords": ["工资", "拖欠", "劳动仲裁", "离职"],
        "rule": "确认劳动关系、工资金额和拖欠周期后，通常先走劳动监察投诉或劳动仲裁。",
        "evidence": ["劳动合同或入职证明", "工资流水", "考勤记录", "催要工资记录"],
    },
    {
        "id": "labor-dismissal",
        "category": "labor",
        "title": "辞退补偿与违法解除",
        "keywords": ["辞退", "解除", "补偿", "赔偿"],
        "rule": "需区分协商解除、过失性解除和违法解除，重点看解除理由、规章制度和通知证据。",
        "evidence": ["解除通知", "规章制度", "绩效/违纪证据", "工资流水"],
    },
    {
        "id": "lease-deposit",
        "category": "lease",
        "title": "租房押金返还争议",
        "keywords": ["押金", "房东", "退租", "墙面", "损坏"],
        "rule": "押金扣除应有合同依据和实际损失证明，正常使用损耗通常不应随意扩大扣费。",
        "evidence": ["租赁合同", "押金转账记录", "交接照片", "维修报价或票据"],
    },
    {
        "id": "debt-wechat",
        "category": "debt",
        "title": "微信聊天记录证明借款",
        "keywords": ["借钱", "欠款", "聊天记录", "转账"],
        "rule": "借款关系可由转账记录、聊天记录、催款记录互相印证，核心是证明借款合意和款项交付。",
        "evidence": ["转账记录", "聊天记录", "催款记录", "对方身份信息"],
    },
    {
        "id": "injury-school-bullying",
        "category": "injury",
        "title": "校园霸凌与学校责任",
        "keywords": ["校园霸凌", "学生", "学校", "受伤"],
        "rule": "需要确认学生年龄、伤害后果、学校是否知道或应当知道，以及学校是否尽到管理职责。",
        "evidence": ["医院诊断", "报警记录", "监控线索", "学校沟通记录"],
    },
    {
        "id": "contract-breach",
        "category": "contract",
        "title": "合同违约责任",
        "keywords": ["合同", "违约", "付款", "交付", "定金"],
        "rule": "先看合同义务、履行期限、违约事实和违约责任条款，再判断继续履行、解除或赔偿。",
        "evidence": ["合同文本", "付款凭证", "交付记录", "催告或解除通知"],
    },
    {
        "id": "marriage-custody",
        "category": "marriage",
        "title": "离婚抚养权争议",
        "keywords": ["离婚", "孩子", "抚养权", "八周岁"],
        "rule": "抚养权通常围绕未成年子女利益判断，孩子年龄、长期照护情况和双方条件都很关键。",
        "evidence": ["孩子年龄证明", "照护记录", "收入居住证明", "协商记录"],
    },
]


def search_similar_cases(query: str, limit: int = 3) -> list[dict[str, Any]]:
    """
    函数作用：
        从内置场景库检索相似法律场景。
    输入参数：
        - query: str
        - limit: int，默认值 3
    输出参数：
        - list[dict[str, Any]]
    """
    intent = classify_legal_intent(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in SCENARIO_CASES:
        score = 0
        if item["category"] == intent["category"]:
            score += 3
        score += sum(1 for keyword in item["keywords"] if keyword in query)
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [dict(item, score=score) for score, item in scored[:limit]]


def format_cases_for_prompt(cases: list[dict[str, Any]]) -> str:
    """
    函数作用：
        将类案场景整理成可注入提示词的短文本。
    输入参数：
        - cases: list[dict[str, Any]]
    输出参数：
        - str
    """
    if not cases:
        return ""
    lines = ["\n\n【相似法律场景参考】"]
    for item in cases:
        evidence = "、".join(item["evidence"])
        lines.append(f"- {item['title']}：{item['rule']} 常见证据：{evidence}。")
    lines.append("以上是场景化分析参考，不是正式判例引用。")
    return "\n".join(lines)
