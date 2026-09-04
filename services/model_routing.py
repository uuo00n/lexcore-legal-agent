"""动态模型路由策略。

路由思想参考 Claude 类智能体常见的分层模式：简单任务走轻量模型，
复杂法律分析、长文档和工具密集场景走更强或更长上下文模型。这里不
依赖外部模型判断，先用可测试的启发式策略完成第一版。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.legal_analysis import analyze_legal_message
from services.model_defaults import resolve_model, resolve_provider


@dataclass(frozen=True)
class ModelRoute:
    """一次模型路由决策。"""
    name: str
    provider: str | None
    model: str | None
    reason: str
    complexity_score: int


COMPLEX_KEYWORDS = [
    "合同审查", "起诉", "仲裁", "刑事", "赔偿", "解除合同", "公司辞退",
    "财产分割", "抚养权", "行政复议", "强制执行", "证据", "诉讼时效",
]


def _route_provider(route_name: str) -> str | None:
    """
    函数作用：
        读取指定路由的 provider 配置，与节点档位共用 services.model_defaults 口径。
    输入参数：
        - route_name: str
    输出参数：
        - str | None
    """
    return resolve_provider(tier=route_name)


def _route_model(route_name: str) -> str:
    """
    函数作用：
        读取指定路由的 model 配置；环境变量都没配时落到档位内置默认模型。
    输入参数：
        - route_name: str
    输出参数：
        - str
    """
    return resolve_model(tier=route_name)


def select_model_route(
    *,
    user_message: str,
    doc_text: str | None = None,
    tool_call_count: int = 0,
) -> ModelRoute:
    """
    函数作用：
        根据问题复杂度、文档长度和工具循环状态选择模型路由。
    输入参数：
        - user_message: str
        - doc_text: str | None，默认值 None
        - tool_call_count: int，默认值 0
    输出参数：
        - ModelRoute
    """
    doc_len = len(doc_text or "")
    analysis = analyze_legal_message(user_message)
    risk_level = analysis["risk"]["level"]
    facts_sufficient = analysis["facts"]["is_sufficient"]
    keyword_hits = [word for word in COMPLEX_KEYWORDS if word in user_message]

    score = 0
    if analysis["intent"]["is_legal"]:
        score += 1
    if risk_level == "medium":
        score += 2
    elif risk_level == "high":
        score += 4
    if not facts_sufficient:
        score += 1
    score += min(3, len(keyword_hits))
    if len(user_message) > 180:
        score += 1
    if doc_len > 6000:
        score += 4
    elif doc_len > 1800:
        score += 2
    if tool_call_count >= 2:
        score += 2

    if doc_len > 6000:
        route_name = "long"
        reason = "uploaded document requires long-context handling"
    elif score >= 5:
        route_name = "strong"
        reason = "legal task is complex or high risk"
    else:
        route_name = "fast"
        reason = "task is short and low complexity"

    return ModelRoute(
        name=route_name,
        provider=_route_provider(route_name),
        model=_route_model(route_name),
        reason=reason,
        complexity_score=score,
    )
