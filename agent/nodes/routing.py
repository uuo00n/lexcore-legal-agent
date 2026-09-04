"""LangGraph conditional edges and tool-result collection."""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage

from agent.agent_names import SPECIALIST_NODES, agent_node
from agent.nodes.evidence_normalizer import normalize_evidence
from agent.state import AgentState
from agent.tool_loop import (
    MAX_TOOL_CALLS,
    MAX_TOOL_CALLS_PER_AGENT,
    MAX_TOOL_CALLS_PER_REQUEST,
)
from agent.repair import MAX_REPAIR_ROUNDS, repair_round_count
from agent.replan import MAX_AGENT_REPLAN_RETRIES, replan_retry_count

log = logging.getLogger(__name__)

# Supervisor 条件边的合法去向；与 ``agent.graph`` 里的 path map 一一对应。
_DISPATCH_TARGETS = frozenset(SPECIALIST_NODES) | {"verify", "end"}


def _dispatch_target(assigned_agent: str) -> str:
    """把计划步骤上的 Agent 名解析成条件边目标；无法识别就终止本轮。

    §四 过渡期里计划可能写规范职责名（``law_retrieval_agent``），也可能写旧
    checkpoint 里的图内节点名，两者都要能落到同一个执行节点。
    """
    target = agent_node(str(assigned_agent or ""))
    return target if target in _DISPATCH_TARGETS else "end"


def should_execute_next(state: AgentState) -> str:
    """Route only to a Specialist, Verifier, or graph end after Supervisor runs."""
    if state.get("supervisor_finalized"):
        return "end"
    route = agent_node(str(state.get("supervisor_route") or ""))
    if route in _DISPATCH_TARGETS:
        return route

    plan = state.get("plan", []) or []
    running = next((step for step in plan if step.get("status") == "running"), None)
    if running is not None:
        return _dispatch_target(running.get("assigned_agent"))
    pending = next((step for step in plan if step.get("status") == "pending"), None)
    if pending is not None:
        return _dispatch_target(pending.get("assigned_agent"))
    if plan:
        return "verify"
    return "end"


def should_after_verifier(state: AgentState) -> str:
    """核验失败优先局部修复（P0-5）；只有问题落不到执行单元时才整体重排。

    两个预算各自独立且都最多一轮：Repair Router 走 ``repair``，计划本身不可修复
    时才走 ``replan``；超预算一律回到 Answer Generator，基于已核验证据作答。
    """
    verification = state.get("verification_result") or {}
    if not verification.get("needs_retry"):
        return "answer_generator"
    route = str(state.get("supervisor_route") or "")
    if route == "repair" and 0 < repair_round_count(state) <= MAX_REPAIR_ROUNDS:
        return "repair"
    if route == "replan" and 0 < replan_retry_count(state) <= MAX_AGENT_REPLAN_RETRIES:
        return "replan"
    return "answer_generator"


def should_after_repair(state: AgentState) -> str:
    """Repair Router 之后的去向：需要重跑 Agent 时回 Supervisor，否则直接重写答案。"""
    return (
        "answer_generator"
        if str(state.get("supervisor_route") or "") == "answer_generator"
        else "supervisor"
    )


def should_after_fact_analysis(state: AgentState) -> str:
    """事实充分性闸门之后的去向（§七、§八、§十九）。

    只有「个案法律结论 + 事实不足」才阻断工作流去补问；通用法律说明属于
    「先答再问」，继续正常流程，缺失事实由 Answer Generator 在答案里提示。
    Intent Router 已经生成最终答复（非法律闲聊）时也不补问，沿用既有终止链路。
    """
    if state.get("supervisor_finalized"):
        return "complexity_router"
    if state.get("needs_clarification") and state.get("clarification_blocking"):
        return "clarification"
    return "complexity_router"


def should_after_complexity(state: AgentState) -> str:
    """复杂度路由之后的去向（§九、§P1-1）。

    ``simple`` 已经由 Complexity Router 写好固定的最小计划，直接交给 Supervisor
    顺序执行，跳过 Planner；其余档位仍然走 Plan-and-Execute。非法律闲聊等已经
    终局的轮次照旧经过 Planner——由它把上一轮遗留的计划清空。
    """
    if state.get("supervisor_finalized"):
        return "planner"
    return "supervisor" if str(state.get("execution_mode") or "") == "simple" else "planner"


def should_continue(state: AgentState) -> str:
    messages = state.get("messages", [])
    if not messages:
        return "end"
    last = messages[-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        if state.get("tool_loop_failure"):
            log.warning("ReAct 循环达到上限 %d 次，返回 Supervisor", MAX_TOOL_CALLS_PER_AGENT)
            return "limit_exceeded"
        return "tools"
    return "end"


def collect_retrieved_laws(state: AgentState) -> dict[str, Any]:
    """Evidence Normalizer 的图节点入口；保留原节点名以兼容既有拓扑与 checkpoint。"""
    return normalize_evidence(state)
