"""澄清补问（Clarification Loop）的确定性决策逻辑（§七、§八、§十五）。

Clarification Node 是普通 Node 而不是 Agent（§六）：追不追问、追问什么，全部由本
模块的规则决定，不调用模型。两条循环必须严格区分——

- Clarification Loop：缺的是**用户事实**，只能问用户；
- Repair Loop：缺的是**执行结果**，由 Repair Router 重跑 Agent（``agent.repair``）。

追问是否阻断，取决于用户要的是哪一种答案：

- 通用法律说明（General Advice）：问规则、门槛、流程 → 先答再问，不阻断；
- 个案法律结论（Individual Legal Conclusion）：问「我能赔多少 / 我能不能赢」
  → 事实不足时必须先补问，否则金额与胜负只能靠编造。

本模块只依赖 ``agent.state`` 与 ``services.legal_analysis`` 的确定性判断，便于单测
直接验证判定口径。
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from agent.state import AgentState
from services.legal_analysis import (
    check_fact_completeness,
    classify_legal_intent,
    is_legal_information_query,
)

# 澄清轮次预算：与 Repair / Replan 预算完全独立，用完后基于已有事实作答，不再往复。
MAX_CLARIFICATION_ROUNDS = max(1, int(os.getenv("MAX_CLARIFICATION_ROUNDS", "2")))

# 要求「个案确定结论」的信号：一旦答案要落到具体数额或胜负，缺失事实就只能靠编造。
_CONCLUSION_DEMAND_PATTERNS = (
    "赔多少", "赔偿多少", "补偿多少", "多少钱", "多少赔偿", "赔几个月", "能赔",
    "能拿到", "能拿多少", "能要回", "能获得", "能主张", "能不能要", "能不能拿",
    "胜诉", "能赢", "会不会输", "判多少", "会判", "要判", "判几年", "值多少",
    "我该怎么", "该怎么办", "怎么维权", "怎么索赔",
)

# 「本人案情」信号：区分个案结论与纯规则问题的关键——没有当事人就没有个案。
_OWN_CASE_PATTERNS = ("我", "我们", "我方", "本人", "咱", "俺")

# 用户已经写了足够长的陈述时不再阻断：继续追问的收益低于打断对话的代价。
_DETAILED_TEXT_LENGTH = 120

# 阻断式追问至少需要缺这么多关键维度；只缺一项时直接作答并在答案里提示补充。
_BLOCKING_MISSING_DIMENSIONS = 2

# 每轮最多向用户提出的问题数：问题过多用户不会回答，反而拖长澄清轮次。
MAX_CLARIFICATION_QUESTIONS = 3

# 按场景与缺失维度给出的具体问题；比「请补充时间相关事实」更可能被用户真正回答。
_QUESTION_TEMPLATES: dict[str, dict[str, str]] = {
    "labor": {
        "劳动关系": "你和公司签的是书面劳动合同还是劳务/外包协议？入职时间和岗位是什么？",
        "时间": "你在这家公司工作了多久？发生争议（如辞退、欠薪）的具体时间是什么时候？",
        "金额": "你的月工资是多少（税前或到手）？公司欠付或已支付的金额是多少？",
        "证据": "你手上有哪些材料？例如劳动合同、工资流水、考勤记录、辞退通知或聊天记录。",
    },
    "lease": {
        "租赁合同": "你和房东签了书面租赁合同吗？合同对押金退还是怎么约定的？",
        "押金/租金": "押金和月租金各是多少？是否有转账记录？",
        "退租原因": "是租期到期正常退租，还是提前解约？房屋是否有损坏或欠费？",
        "证据": "你有哪些材料？例如租赁合同、转账记录、退房时的照片或与房东的聊天记录。",
    },
    "debt": {
        "借款关系": "你们之间有借条、欠条或书面协议吗？钱是怎么交付的（转账还是现金）？",
        "金额": "借款本金是多少？是否约定了利息？对方已经还了多少？",
        "期限": "约定的还款时间是什么时候？对方逾期多久了？",
        "证据": "你有哪些材料？例如借条、转账记录、催款的聊天记录或通话录音。",
    },
    "injury": {
        "主体身份": "双方是什么身份和年龄（例如是否为未成年人、是否在校学生、是否同事）？",
        "伤害后果": "受伤的具体情况如何？是否就医、住院或做过伤情鉴定？",
        "行为经过": "事情的经过是怎样的？发生在什么时间、什么场所？",
        "证据": "你有哪些材料？例如监控、报警记录、就诊病历、鉴定意见或聊天记录。",
    },
    "contract": {
        "合同内容": "合同或协议是怎么约定的？关键条款（标的、价款、期限）分别是什么？",
        "履行情况": "双方各自履行到什么程度？付款和交付分别完成了多少？",
        "违约事实": "对方的违约行为具体是什么？发生在什么时间？",
        "证据": "你有哪些材料？例如合同原件、付款凭证、发票、往来邮件或聊天记录。",
    },
    "marriage": {
        "婚姻状态": "目前是登记结婚、分居还是已经起诉离婚？登记时间是什么时候？",
        "子女/财产": "是否有未成年子女？主要争议财产（房产、存款、彩礼）有哪些？",
        "争议目标": "你希望达成什么结果？例如离婚、争取抚养权还是分割某项财产。",
        "证据": "你有哪些材料？例如结婚证、房产证、银行流水、转账记录或书面协议。",
    },
    "criminal": {
        "行为类型": "具体行为是什么（例如种植、持有、买卖、运输）？由谁实施？",
        "数量": "涉及的数量或重量大概是多少？",
        "对象": "涉及的具体对象或物品是什么？",
        "处理状态": "目前处于什么状态？例如已被查处、已自行处理，还是尚未发生。",
    },
}

# 跨场景通用维度的兜底问法，避免同一维度在每个场景里重复维护。
_SHARED_QUESTIONS: dict[str, str] = {
    "证据": "你手上有哪些能证明上述事实的材料？例如书面文件、转账记录、聊天记录或录音。",
    "时间": "关键事件发生的具体时间是什么时候？",
    "金额": "涉及的金额大概是多少？",
}

_GENERIC_QUESTION = "请补充「{dimension}」相关的关键事实。"


def clarification_round_count(state: AgentState | Mapping[str, object]) -> int:
    """已经向用户发起的补问轮次；旧 checkpoint 缺该字段时按 0 处理。"""
    return max(0, int(state.get("clarification_round") or 0))


def demands_individual_conclusion(text: str) -> bool:
    """判断用户是否在要求针对本人案情的确定结论（金额、胜负、具体做法）。

    只有同时出现「本人案情」与「确定结论」信号才算：``是否构成犯罪``、
    ``辞退赔多少钱`` 这类纯规则问题应当直接作答，不该被追问打断。
    """
    normalized = (text or "").strip()
    if not normalized:
        return False
    if not any(pattern in normalized for pattern in _OWN_CASE_PATTERNS):
        return False
    return any(pattern in normalized for pattern in _CONCLUSION_DEMAND_PATTERNS)


def clarification_question_for(category: str, dimension: str) -> str:
    """把缺失的事实维度翻译成用户能直接回答的问题。"""
    scoped = _QUESTION_TEMPLATES.get(category, {})
    return (
        scoped.get(dimension)
        or _SHARED_QUESTIONS.get(dimension)
        or _GENERIC_QUESTION.format(dimension=dimension)
    )


def clarification_questions_for(category: str, dimensions: Iterable[str]) -> list[str]:
    """按缺失维度生成去重、有上限的问题列表。"""
    questions: list[str] = []
    for dimension in dimensions or []:
        question = clarification_question_for(category, str(dimension))
        if question not in questions:
            questions.append(question)
        if len(questions) >= MAX_CLARIFICATION_QUESTIONS:
            break
    return questions


@dataclass
class ClarificationDecision:
    """一次事实充分性判定的完整结论。"""

    facts_sufficient: bool = True
    needs_clarification: bool = False
    blocking: bool = False
    questions: list[str] = field(default_factory=list)
    missing_facts: list[str] = field(default_factory=list)
    category: str = "general"
    is_legal: bool = False
    reason: str = ""

    @property
    def must_ask_first(self) -> bool:
        """必须先补问再继续：这是唯一会中断工作流的情形（§八 个案法律结论）。"""
        return self.needs_clarification and self.blocking


def facts_text(confirmed_facts: Mapping[str, object] | None) -> str:
    """把跨轮已确认事实拼成一段可供确定性检查扫描的文本。"""
    parts: list[str] = []
    for key, value in (confirmed_facts or {}).items():
        if isinstance(value, (list, tuple, set)):
            rendered = "、".join(str(item) for item in value if str(item).strip())
        else:
            rendered = str(value or "").strip()
        if rendered:
            parts.append(f"{key}：{rendered}" if not str(key).startswith("_") else rendered)
    return "\n".join(parts)


def decide_clarification(
    question: str,
    *,
    confirmed_facts: Mapping[str, object] | None = None,
    has_uploaded_doc: bool = False,
    round_count: int = 0,
) -> ClarificationDecision:
    """判断事实是否充分、是否补问、补问能否阻断（§七、§八、§十五）。

    充分性检查跑在「本轮问题 + 跨轮已确认事实」的合并文本上：否则用户第二轮
    只回一句「3 年」时，事实看起来比第一轮更少，澄清会永远收不了口。
    意图与「个案结论」判定仍只看问题本身，terse 的补充回答不会改变问题性质。
    """
    question = (question or "").strip()
    merged_facts = facts_text(confirmed_facts)
    combined = "\n".join(part for part in (question, merged_facts) if part)
    intent = classify_legal_intent(question or combined)
    completeness = check_fact_completeness(combined or question)
    category = str(completeness.get("category") or intent.get("category") or "general")
    missing = [str(item) for item in completeness.get("missing_dimensions", []) or []]
    is_legal = bool(intent.get("is_legal"))
    base = ClarificationDecision(category=category, is_legal=is_legal, missing_facts=missing)

    if not is_legal:
        return ClarificationDecision(category=category, reason="not_legal")
    if has_uploaded_doc:
        # 上传文档本身就是事实来源，先读文档再追问，否则会问用户文档里已有的信息。
        return ClarificationDecision(category=category, is_legal=True, reason="uploaded_doc")
    if round_count >= MAX_CLARIFICATION_ROUNDS:
        # 预算用尽：基于已有事实作答，答案里仍然保留「需要补充的信息」。
        base.facts_sufficient = False
        base.reason = "clarification_budget_exhausted"
        return base
    if completeness.get("is_sufficient"):
        base.reason = "facts_sufficient"
        return base

    own_case = any(pattern in question for pattern in _OWN_CASE_PATTERNS)
    if is_legal_information_query(question) and not own_case:
        # 纯规则、门槛、流程问题：直接作答，不追问（§五、§二 问题 1）。
        base.reason = "legal_information_query"
        return base

    base.facts_sufficient = False
    base.needs_clarification = True
    base.questions = clarification_questions_for(category, missing)
    base.blocking = (
        demands_individual_conclusion(question)
        and len(missing) >= _BLOCKING_MISSING_DIMENSIONS
        and len(combined) < _DETAILED_TEXT_LENGTH
    )
    base.reason = "individual_conclusion" if base.blocking else "general_advice"
    return base


__all__ = [
    "MAX_CLARIFICATION_QUESTIONS",
    "MAX_CLARIFICATION_ROUNDS",
    "ClarificationDecision",
    "clarification_question_for",
    "clarification_questions_for",
    "clarification_round_count",
    "decide_clarification",
    "demands_individual_conclusion",
    "facts_text",
]
