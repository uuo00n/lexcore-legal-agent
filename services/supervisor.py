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


AgentRoute = Literal["case_analysis_agent", "statute_retrieval_agent", "legal_consult_agent", "final"]


@dataclass(frozen=True)
class SupervisorDecision:
    """中控智能体路由决策。"""
    route: AgentRoute
    reason: str
    complexity: str
    need_tools: bool


STATUTE_QUERY_KEYWORDS = ["法条", "法律规定", "司法解释", "哪一条", "依据", "构成要件", "处罚标准", "期限", "几株", "多少算", "标准"]
SIMPLE_CONSULT_KEYWORDS = ["是什么", "去哪里", "哪里申请", "怎么申请", "流程", "材料", "条件", "需要什么"]
SUPERVISOR_PROMPT = """你是法律智能体系统的中控智能体，负责路由；只有非法律寒暄、情绪表达等简单场景可以直接回复。

你必须只输出 JSON，不要输出 Markdown。JSON 字段：
- route: case_analysis_agent | statute_retrieval_agent | legal_consult_agent | final
- reason: 简短中文理由
- complexity: low | medium | high
- need_tools: true | false

路由规则：
1. 先判断事实是否足以给出有用的初步法律分析。
   - 涉及具体事件、主体行为、责任、请求权、抗辩或证据 -> case_analysis_agent。
   - 纯法条、司法解释、法律依据、处罚门槛问题 -> statute_retrieval_agent。
   - 已有结构化事实与法规报告、仅需解释和行动建议 -> legal_consult_agent。
2. Contract Agent 当前不在主路由中；合同问题也不得 route=contract_agent。
3. 非法律寒暄、情绪表达、简单陪伴或闲聊 -> final。
4. 每项工作只分配给一个 Specialist Agent，不得让多个 Agent 重复提取事实或重复检索。
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
    follow_up = should_ask_follow_up(text, has_uploaded_doc=has_uploaded_doc)
    if follow_up["should_ask"]:
        return SupervisorDecision(
            route="case_analysis_agent",
            reason="具体案件需要先由案件分析智能体提取事实并识别证据缺口",
            complexity="low",
            need_tools=False,
        )

    if any(keyword in text for keyword in SIMPLE_CONSULT_KEYWORDS):
        return SupervisorDecision(
            route="legal_consult_agent",
            reason="用户主要询问办理路径或法律概念，由法律咨询智能体解释和给出行动建议",
            complexity="low",
            need_tools=True,
        )

    if any(keyword in text for keyword in STATUTE_QUERY_KEYWORDS):
        return SupervisorDecision(
            route="statute_retrieval_agent",
            reason="用户主要询问法规、司法解释或明确法律依据",
            complexity="low",
            need_tools=True,
        )

    intent = classify_legal_intent(text)
    if intent["is_legal"]:
        return SupervisorDecision(
            route="case_analysis_agent",
            reason="具体法律问题先由案件分析智能体整理事实、关系、争点与证据",
            complexity="medium" if intent["category"] != "general" else "low",
            need_tools=False,
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
    if route not in {"case_analysis_agent", "statute_retrieval_agent", "legal_consult_agent", "final"}:
        raise ValueError(f"invalid supervisor route: {route!r}")
    complexity = data.get("complexity") or "low"
    if complexity not in {"low", "medium", "high"}:
        complexity = "low"
    need_tools = route in {"statute_retrieval_agent", "legal_consult_agent"}
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
            provider=os.getenv("SUPERVISOR_PROVIDER", "deepseek"),
            model=os.getenv("SUPERVISOR_MODEL", "deepseek-v4-flash-vision-exp"),
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
