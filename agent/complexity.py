"""Complexity Router 的确定性判定（§九、§P1-1、§五）。

Complexity Router 是普通 Node 而不是 Agent（§六）：走哪条执行路径完全由本模块的
规则决定，不调用模型。三档复杂度对应两条执行路径——

- ``simple``：单一法律关系、单一争议焦点的问题，直接用固定的最小计划执行
  （法规检索 → 法律推理），跳过 Planner 与重新规划（§P1-1、§二 问题 1）；
- ``medium`` / ``complex``：仍然走 Plan-and-Execute，由 Planner 拆解步骤。

类案检索只在用户明确要求、或问题本身依赖司法实践口径时才安排（§五）：简单法条
咨询默认不查案例，这是 §三十 用例 1 的硬性要求。

本模块只依赖 ``services.legal_analysis`` 的确定性判断，便于单测直接验证口径。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from services.legal_analysis import LEGAL_KEYWORDS

# 明确要求类案／司法实践的信号（§五）：只有命中这些词才安排案例检索步骤。
_CASE_DEMAND_PATTERNS = (
    "判例", "案例", "类案", "先例", "同类案件", "类似案子", "类似案件", "有没有类似",
    "司法实践", "司法实务", "实务中", "实践中", "裁判", "判决书", "法院怎么判",
    "法院一般怎么判", "法院会怎么判", "怎么判的", "判过", "胜诉率",
)

# 一次提问里塞了多个诉求的连接词：这类问题需要 Planner 拆步骤，走不了固定最小计划。
_MULTI_DEMAND_PATTERNS = (
    "另外", "此外", "除此之外", "同时还", "还想问", "还有一个问题", "第二个问题",
    "顺便问", "以及是否", "并且还", "两件事", "几个问题",
)

# 牵涉多方主体或多层责任：事实结构本身需要结构化拆解。
_MULTI_PARTY_PATTERNS = ("连带责任", "第三方", "多方", "两家公司", "多家公司", "转包", "分包")

# 超过这个长度的提问基本是一段案情叙述，需要 Planner 先拆解事实与争议焦点。
_NARRATIVE_TEXT_LENGTH = 180

# 争议焦点数量阈值：一个焦点走简单路径，两个走 Plan，三个以上按复杂处理。
_MEDIUM_ISSUE_COUNT = 2
_COMPLEX_ISSUE_COUNT = 3

# 复杂度到既有 ``task_complexity`` 字段的映射；保留旧口径以免影响现有 Planner 分支。
_LEGACY_COMPLEXITY = {"simple": "low", "medium": "medium", "complex": "high"}


@dataclass
class ComplexityDecision:
    """一次复杂度判定的完整结论。"""

    level: str = "simple"
    needs_case_retrieval: bool = False
    reason: str = "single_issue"
    signals: list[str] = field(default_factory=list)

    @property
    def execution_mode(self) -> str:
        """``simple`` 走固定最小计划，其余一律走 Plan-and-Execute。"""
        return "simple" if self.level == "simple" else "plan"

    @property
    def legacy_task_complexity(self) -> str:
        """既有 ``task_complexity`` 字段的取值，保持 low/medium/high 口径不变。"""
        return _LEGACY_COMPLEXITY.get(self.level, "medium")


def demands_case_retrieval(text: str) -> bool:
    """用户是否明确要求类案、判例或司法实践口径（§五）。"""
    normalized = (text or "").strip()
    return bool(normalized) and any(pattern in normalized for pattern in _CASE_DEMAND_PATTERNS)


def _legal_issue_count(case_facts: Mapping[str, object] | None) -> int:
    issues = (case_facts or {}).get("legal_issues") or []
    if not isinstance(issues, (list, tuple)):
        return 0
    return len({str(item).strip() for item in issues if str(item).strip()})


def _matched_categories(text: str) -> int:
    """命中关键词的法律领域数量；跨领域提问不适合固定最小计划。"""
    return sum(
        1
        for keywords in LEGAL_KEYWORDS.values()
        if any(keyword in text for keyword in keywords)
    )


def decide_complexity(
    question: str,
    *,
    is_legal: bool = True,
    has_uploaded_doc: bool = False,
    case_facts: Mapping[str, object] | None = None,
    router_complexity: str = "",
    clarification_exhausted: bool = False,
) -> ComplexityDecision:
    """判断本轮该走简单路径还是 Plan-and-Execute（§九、§P1-1）。

    判定顺序是「先看能不能升级成复杂，再看要不要升到 medium，剩下的才是 simple」，
    因为误判成复杂只是回到既有行为，误判成简单会让问题被草率处理。
    """
    normalized = (question or "").strip()
    signals: list[str] = []
    case_demand = demands_case_retrieval(normalized)
    issue_count = _legal_issue_count(case_facts)

    if not normalized or not is_legal:
        # 非法律请求不进简单路径：固定最小计划会给闲聊安排法规检索。
        return ComplexityDecision(level="medium", reason="not_legal")

    if has_uploaded_doc:
        signals.append("uploaded_doc")
    if case_demand:
        signals.append("case_demand")
    if str(router_complexity or "").lower() == "high":
        signals.append("router_high")
    if issue_count >= _COMPLEX_ISSUE_COUNT:
        signals.append("many_legal_issues")
    if signals:
        return ComplexityDecision(
            level="complex",
            needs_case_retrieval=case_demand,
            reason=signals[0],
            signals=signals,
        )

    if issue_count >= _MEDIUM_ISSUE_COUNT:
        signals.append("multiple_legal_issues")
    if any(pattern in normalized for pattern in _MULTI_DEMAND_PATTERNS):
        signals.append("multiple_demands")
    if any(pattern in normalized for pattern in _MULTI_PARTY_PATTERNS):
        signals.append("multiple_parties")
    if len(normalized) >= _NARRATIVE_TEXT_LENGTH:
        signals.append("long_narrative")
    if _matched_categories(normalized) >= 2:
        signals.append("cross_domain")
    if clarification_exhausted:
        # 补问预算已用尽还缺事实：答案要讲清「缺什么、影响哪一部分」，交给 Planner 拆解。
        signals.append("clarification_exhausted")
    if signals:
        return ComplexityDecision(level="medium", reason=signals[0], signals=signals)

    return ComplexityDecision(level="simple", reason="single_issue")


__all__ = [
    "ComplexityDecision",
    "decide_complexity",
    "demands_case_retrieval",
]
