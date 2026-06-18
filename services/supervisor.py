"""Supervisor 多智能体路由。

中控智能体负责任务路由；对非法律寒暄、情绪表达等简单场景，可以直接最终回复。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage

from services.legal_analysis import classify_legal_intent, should_ask_follow_up
from services.llm import get_llm


AgentRoute = Literal["fact_agent", "contract_agent", "legal_consult_agent", "final"]


@dataclass(frozen=True)
class SupervisorDecision:
    """中控智能体路由决策。"""
    route: AgentRoute
    reason: str
    complexity: str
    need_tools: bool


CONTRACT_KEYWORDS = ["合同", "协议", "条款", "审查", "违约金", "甲方", "乙方", "签约"]
SIMPLE_CONSULT_KEYWORDS = ["是什么", "去哪里", "哪里申请", "怎么申请", "流程", "期限", "材料", "条件", "需要什么"]
SUPERVISOR_PROMPT = """你是法律智能体系统的中控智能体，负责路由；只有非法律寒暄、情绪表达等简单场景可以直接回复。

你必须只输出 JSON，不要输出 Markdown。JSON 字段：
- route: fact_agent | contract_agent | legal_consult_agent | final
- reason: 简短中文理由
- complexity: low | medium | high
- need_tools: true | false

路由规则：
1. 先判断事实是否足以给出有用的初步法律分析。
   - 只有缺少关键事实导致无法给出任何有意义的初步判断时，才 route=fact_agent。
   - 如果已经能基于现有事实给出初步结论、法律依据、计算方式或条件分支，即使仍需补充证据/金额/日期，也 route=legal_consult_agent，并在回答中说明需补充的信息。
   - 纯法条、概念、流程、处罚门槛类问题通常不需要先追问事实。
2. 合同/协议审查、用户上传合同文档、要求看合同有没有坑 -> contract_agent。
3. 普通法律咨询、概念解释、流程问题、事实基本够的问题 -> legal_consult_agent。
4. 非法律寒暄、情绪表达、简单陪伴或闲聊 -> final。
5. 法条检索不是独立智能体，由 legal_consult_agent 通过工具服务完成。
"""


def route_user_request(
    *,
    message: str,
    has_uploaded_doc: bool = False,
    uploaded_doc_name: str | None = None,
) -> SupervisorDecision:
    """
    函数作用：
        根据用户问题和文档上下文选择业务 Agent。
    输入参数：
        - message: str
        - has_uploaded_doc: bool，默认值 False
        - uploaded_doc_name: str | None，默认值 None
    输出参数：
        - SupervisorDecision
    """
    text = message.strip()
    doc_name = uploaded_doc_name or ""
    is_contract = any(keyword in text or keyword in doc_name for keyword in CONTRACT_KEYWORDS)
    asks_review = any(keyword in text for keyword in ["看看", "审", "有没有坑", "修改", "风险条款"])
    if has_uploaded_doc and (is_contract or asks_review):
        return SupervisorDecision(
            route="contract_agent",
            reason="用户上传了文档并表达合同/协议审查意图",
            complexity="medium",
            need_tools=False,
        )
    if is_contract and asks_review:
        return SupervisorDecision(
            route="contract_agent",
            reason="用户问题属于合同审查场景",
            complexity="medium",
            need_tools=False,
        )

    follow_up = should_ask_follow_up(text, has_uploaded_doc=has_uploaded_doc)
    if follow_up["should_ask"]:
        return SupervisorDecision(
            route="fact_agent",
            reason="法律问题缺少关键事实，先由事实审查智能体追问",
            complexity="low",
            need_tools=False,
        )

    if any(keyword in text for keyword in SIMPLE_CONSULT_KEYWORDS):
        return SupervisorDecision(
            route="legal_consult_agent",
            reason="用户询问概念或流程，事实充分性检查未要求追问",
            complexity="low",
            need_tools=True,
        )

    intent = classify_legal_intent(text)
    if intent["is_legal"]:
        return SupervisorDecision(
            route="legal_consult_agent",
            reason="事实足以进入法律咨询智能体，结合 RAG 和工具生成解决思路",
            complexity="medium" if intent["category"] != "general" else "low",
            need_tools=True,
        )

    return SupervisorDecision(
        route="final",
        reason="非法律或通用表达，由主控智能体直接回复",
        complexity="low",
        need_tools=False,
    )


def _parse_llm_decision(content: str) -> SupervisorDecision:
    """
    函数作用：
        从中控智能体输出中解析路由 JSON。
    输入参数：
        - content: str
    输出参数：
        - SupervisorDecision
    """
    match = re.search(r"\{.*\}", content, flags=re.S)
    if not match:
        raise ValueError("supervisor did not return JSON")
    data = json.loads(match.group(0))
    route = data.get("route")
    if route not in {"fact_agent", "contract_agent", "legal_consult_agent", "final"}:
        raise ValueError(f"invalid supervisor route: {route!r}")
    complexity = data.get("complexity") or "low"
    if complexity not in {"low", "medium", "high"}:
        complexity = "low"
    need_tools = route == "legal_consult_agent"
    return SupervisorDecision(
        route=route,
        reason=str(data.get("reason") or "LLM 中控智能体完成路由"),
        complexity=complexity,
        need_tools=need_tools,
    )


async def route_user_request_with_llm(
    *,
    message: str,
    has_uploaded_doc: bool = False,
    uploaded_doc_name: str | None = None,
    trace_id: str | None = None,
    thread_id: str | None = None,
) -> SupervisorDecision:
    """
    函数作用：
        使用大模型中控智能体做路由，失败时降级到规则路由。
    输入参数：
        - message: str
        - has_uploaded_doc: bool，默认值 False
        - uploaded_doc_name: str | None，默认值 None
        - trace_id: str | None，默认值 None
        - thread_id: str | None，默认值 None
    输出参数：
        - SupervisorDecision
    """
    context = {
        "message": message,
        "has_uploaded_doc": has_uploaded_doc,
        "uploaded_doc_name": uploaded_doc_name or "",
    }
    try:
        llm = get_llm(
            provider=os.getenv("SUPERVISOR_PROVIDER", "zhipu"),
            model=os.getenv("SUPERVISOR_MODEL", "GLM-4.6V"),
            model_route="supervisor_agent",
            trace_id=trace_id,
            thread_id=thread_id,
            temperature=0,
            streaming=False,
        )
        response = await llm.ainvoke([
            SystemMessage(content=SUPERVISOR_PROMPT),
            HumanMessage(content=json.dumps(context, ensure_ascii=False)),
        ])
        return _parse_llm_decision(response.content or "")
    except Exception as exc:
        deterministic = route_user_request(
            message=message,
            has_uploaded_doc=has_uploaded_doc,
            uploaded_doc_name=uploaded_doc_name,
        )
        return SupervisorDecision(
            route=deterministic.route,
            reason=f"LLM 中控路由失败，已使用规则兜底：{exc}; {deterministic.reason}",
            complexity=deterministic.complexity,
            need_tools=deterministic.need_tools,
        )
