"""Repair Router 节点：核验失败后只重跑受影响的步骤（§P0-5、P0-6）。"""
from __future__ import annotations

from typing import Any

from agent.node_utils import record_trace_event
from agent.repair import (
    ANSWER_TARGET,
    MAX_REPAIR_ROUNDS,
    RepairPlan,
    plan_repair,
    repair_round_count,
)
from agent.state import AgentState, PlanStep
from agent.tool_loop import request_budget_exhausted
from services.workflow_metrics import record_repair


def _plan_views(plan: list[PlanStep]) -> tuple[list[PlanStep], list[PlanStep]]:
    completed = [dict(step) for step in plan if step.get("status") == "completed"]
    remaining = [dict(step) for step in plan if step.get("status") == "pending"]
    return completed, remaining


def _repair_updates(repair: RepairPlan) -> dict[str, Any]:
    """重开受影响的步骤，并清掉会干扰重跑的执行痕迹。

    ``retrieved_laws`` / ``retrieved_cases`` / ``verified_evidence`` /
    ``agent_reports`` 一律不动：第一轮已核验的证据必须原样保留（P0-6），被重开
    步骤的报告会由 ``merge_agent_reports`` 按同一 ``report_id`` 覆盖。

    ``tool_refresh_allowed`` 是 §二十二 里「Repair Router 要求刷新」的开关：修复
    本来就是为了重新检索被质疑的条文，这一轮允许重复同一检索签名，也不因为证据
    已到量就拒绝检索；``evidence_gain`` 停止条件仍然有效，刷新后没有新证据就停。
    刷新绕不过全请求工具预算（``MAX_TOOL_CALLS_PER_REQUEST``）——``tool_call_total``
    刻意不在这里归零，只有 ``tool_call_count`` 按步骤语义重置。
    """
    completed, remaining = _plan_views(repair.plan)
    return {
        "plan": repair.plan,
        "current_step": None,
        "completed_steps": completed,
        "remaining_steps": remaining,
        "retry_count": 0,
        "tool_call_count": 0,
        "tool_loop_failure": None,
        "tool_refresh_allowed": True,
        "supervisor_route": "repair",
        "supervisor_finalized": False,
    }


def repair_router_node(state: AgentState) -> dict[str, Any]:
    """按核验问题类型路由到最小修复单元；不调用模型，也不改写任何结论。

    全请求工具预算耗尽时，需要重新检索的修复一律不发起：重开步骤也拿不到新证据，
    报告不会变，只是白花一轮 Specialist + Verifier 调用。纯答案格式问题不受影响。
    """
    verification = dict(state.get("verification_result") or {})
    repair = plan_repair(state, verification)
    repair_count = repair_round_count(state)
    verification["repair_targets"] = list(repair.targets)
    # 只有需要重新检索的修复才受工具预算约束；答案重写不调工具。
    budget_blocked = not repair.answer_only and request_budget_exhausted(state)
    payload = {
        "targets": repair.targets,
        "reopened_steps": repair.reopened,
        "added_steps": repair.added,
        "unroutable_issues": repair.unroutable,
        "repair_count": repair_count,
        "request_budget_exhausted": budget_blocked,
    }

    if not repair.repairable or repair_count > MAX_REPAIR_ROUNDS or budget_blocked:
        # 没有可路由的问题（例如计划本身失败）、修复预算已用尽，或全请求工具预算已耗尽：
        # 交回答案生成，由 Answer Generator 基于已核验证据作答，不再触发整体重跑。
        record_trace_event(
            state.get("trace_id"),
            "repair_skipped",
            name="repair_router",
            payload=payload,
        )
        if not repair.repairable:
            reason = "核验问题无对应修复目标，基于已核验证据作答"
        elif repair_count > MAX_REPAIR_ROUNDS:
            reason = "修复预算已用尽，基于已核验证据作答"
        else:
            reason = "全请求工具预算已耗尽，无法重新检索，基于已核验证据作答"
        return {
            "verification_result": verification,
            "supervisor_route": ANSWER_TARGET,
            "supervisor_reason": reason,
            "supervisor_finalized": False,
        }

    record_trace_event(
        state.get("trace_id"),
        "repair_started",
        name="repair_router",
        payload={**payload, "reason": repair.reason},
    )
    # §二十五：只统计真正发起的局部修复；预算耗尽的 repair_skipped 不算一次修复。
    record_repair(list(repair.targets))
    if repair.answer_only:
        # 只有答案格式类问题：直接重写答案，不重跑任何 Agent。
        return {
            "verification_result": verification,
            "supervisor_route": ANSWER_TARGET,
            "supervisor_reason": f"仅需重写答案：{repair.reason}" if repair.reason else "仅需重写答案",
            "supervisor_finalized": False,
        }

    result = _repair_updates(repair)
    result["verification_result"] = verification
    result["supervisor_reason"] = (
        f"局部修复 {'、'.join(repair.targets)}：{repair.reason}"
        if repair.reason
        else f"局部修复 {'、'.join(repair.targets)}"
    )
    return result


__all__ = ["repair_router_node"]
