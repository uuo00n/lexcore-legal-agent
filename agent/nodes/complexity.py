"""Complexity Router 节点（§九、§P1-1、§十九）。

普通 Node（§六）：不调用模型。它做两件事——

1. 把本轮的复杂度定档，写入 ``complexity_level`` / ``execution_mode``，
   并同步既有的 ``task_complexity`` 字段，让复杂度只有一个真相源（§二 问题 12）；
2. 简单路径直接生成固定的最小计划（法规检索 → 法律推理）并交给 Supervisor 顺序
   执行，从而跳过 Planner 与整体重排（§P1-1、§二 问题 1、§二十六 延迟目标）。

固定计划仍然走既有的 Supervisor / Specialist / Evidence Normalizer 链路：简单路径
省掉的是「规划」与「多智能体扇出」，而不是执行与核验，这样两条路径的证据口径、
引用核验和答案生成完全一致（§P0-1）。
"""
from __future__ import annotations

from typing import Any

from agent.complexity import ComplexityDecision, decide_complexity
from agent.node_utils import effective_question, record_trace_event
from agent.state import AgentState, PlanStep, TaskType
from services.legal_analysis import classify_legal_intent
from services.workflow_metrics import record_complexity_route

# 简单路径的固定计划：只检索法条、只做一次法律推理，不查类案（§五、§三十 用例 1）。
_SIMPLE_PLAN_TASKS: tuple[tuple[TaskType, str, str], ...] = (
    (TaskType.STATUTE_RETRIEVAL, "检索与问题直接相关的现行法律依据", "statute_retrieval_agent"),
    (TaskType.LEGAL_CONSULTATION, "结合检索到的法律依据给出可执行的法律建议", "legal_consult_agent"),
)

# 意图字段里代表「非法律」的取值；与 Planner 保持同一口径。
_NON_LEGAL_INTENTS = {"chat", "chitchat", "non_legal", "non-legal", "nonlegal", "闲聊", "非法律"}


def _is_legal(state: AgentState, question: str) -> bool:
    intent = str(state.get("intent") or "").strip().lower()
    if intent and intent in _NON_LEGAL_INTENTS:
        return False
    if intent:
        return True
    return bool(classify_legal_intent(question).get("is_legal"))


def _simple_plan() -> list[PlanStep]:
    return [
        {
            "step_id": f"step_{index}",
            "task_type": task_type,
            "description": description,
            "assigned_agent": agent_name,
            "status": "pending",
            "required": True,
        }
        for index, (task_type, description, agent_name) in enumerate(_SIMPLE_PLAN_TASKS, start=1)
    ]


def _decide(state: AgentState, question: str) -> ComplexityDecision:
    facts = state.get("case_facts") or {}
    return decide_complexity(
        question,
        is_legal=_is_legal(state, question),
        has_uploaded_doc=bool(state.get("uploaded_doc_text")),
        case_facts=facts,
        router_complexity=str(state.get("task_complexity") or ""),
        clarification_exhausted=(
            not bool(state.get("facts_sufficient", True))
            and not bool(state.get("needs_clarification"))
        ),
    )


def complexity_router_node(state: AgentState) -> dict[str, Any]:
    """给本轮定复杂度档位，并为简单路径准备固定的最小计划（§九、§P1-1）。"""
    if state.get("supervisor_finalized"):
        # Intent Router 已经给出最终答复（非法律闲聊等），不需要选执行路径。
        return {}

    question = effective_question(state)
    decision = _decide(state, question)
    result: dict[str, Any] = {
        "complexity_level": decision.level,
        "execution_mode": decision.execution_mode,
        "needs_case_retrieval": decision.needs_case_retrieval,
        # 复杂度只保留一个真相源：既有字段跟着路由结论走，不再各处自行判断（§二 问题 12）。
        "task_complexity": decision.legacy_task_complexity,
    }
    if decision.execution_mode == "simple":
        plan = _simple_plan()
        result["plan"] = plan
        result["remaining_steps"] = [dict(step) for step in plan]

    record_trace_event(
        state.get("trace_id"),
        "complexity_route",
        name="complexity_router",
        payload={
            "complexity_level": decision.level,
            "execution_mode": decision.execution_mode,
            "needs_case_retrieval": decision.needs_case_retrieval,
            "reason": decision.reason,
            "signals": decision.signals,
            "plan_steps": len(result.get("plan", []) or []),
        },
    )
    # §二十五：简单路径占比与类案检索触发率只能从这里统计。
    record_complexity_route(
        complexity_level=decision.level,
        execution_mode=decision.execution_mode,
        needs_case_retrieval=decision.needs_case_retrieval,
    )
    return result


__all__ = ["complexity_router_node"]
