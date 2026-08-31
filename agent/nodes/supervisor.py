"""Supervisor router and deterministic plan executor."""
from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.node_utils import compatibility_dependency, latest_human_message, record_trace_event
from agent.prompts import SUPERVISOR_DIRECT_PROMPT
from agent.reports import report_agent_name
from agent.state import AgentState, PlanStep
from services.answer_format import strip_answer_markdown
from services.legal_analysis import classify_legal_intent, score_legal_answer
from services.llm import get_llm
from services.supervisor import route_user_request_with_llm


SPECIALIST_AGENTS = {
    "case_analysis_agent",
    "statute_retrieval_agent",
    "legal_consult_agent",
}
MAX_STEP_RETRIES = int(os.getenv("MAX_STEP_RETRIES", "1"))


def _last_report_from(state: AgentState, agent_name: str) -> dict[str, Any] | None:
    for report in reversed(state.get("agent_reports", []) or []):
        if report_agent_name(report) == agent_name:
            return report
    return None


def _next_route_after_agent_reports(state: AgentState) -> tuple[str, str]:
    """Compatibility helper for callers that still do not submit a plan."""
    latest = (state.get("agent_reports", []) or [])[-1]
    agent = report_agent_name(latest)
    status = latest.get("status")
    if agent == "case_analysis_agent" and status == "needs_more_facts":
        return "end", "案件分析智能体确认关键事实不足，由主控向用户追问"
    if agent == "case_analysis_agent" and _last_report_from(state, "statute_retrieval_agent") is None:
        return "statute_retrieval_agent", "案件结构已整理，分配独立法规检索任务"
    if agent == "statute_retrieval_agent" and _last_report_from(state, "legal_consult_agent") is None:
        return "legal_consult_agent", "法规报告已完成，交由法律咨询智能体综合解释和行动建议"
    return "end", f"{agent or '专家智能体'} 已返回报告"


def _report_for_running_step(
    state: AgentState,
    step: PlanStep,
) -> dict[str, Any] | None:
    """Find the report produced by this exact specialist invocation."""
    step_id = str(step.get("step_id") or "")
    assigned_agent = str(step.get("assigned_agent") or "")
    reports = list(state.get("agent_reports", []) or [])
    for report in reversed(reports):
        task_id = str(report.get("task_id") or report.get("step_id") or "")
        if task_id == step_id and report_agent_name(report) == assigned_agent:
            return report

    used_report_ids = {
        str(result.get("report_id") or "")
        for item in state.get("plan", []) or []
        if item.get("status") == "completed"
        for result in [item.get("result")]
        if isinstance(result, dict)
    }
    for report in reversed(reports):
        if report_agent_name(report) != assigned_agent:
            continue
        report_id = str(report.get("report_id") or "")
        if report_id and report_id in used_report_ids:
            continue
        return report
    return None


def _plan_views(plan: list[PlanStep]) -> tuple[list[PlanStep], list[PlanStep]]:
    completed = [dict(step) for step in plan if step.get("status") == "completed"]
    remaining = [
        dict(step)
        for step in plan
        if step.get("status") in {"pending", "running"}
    ]
    return completed, remaining


def _executor_update(state: AgentState) -> dict[str, Any]:
    """Collect one result, choose one pending step, and emit state-only updates."""
    plan: list[PlanStep] = [dict(step) for step in state.get("plan", []) or []]  # type: ignore[misc]
    current_step_id = str(state.get("current_step") or "")
    retry_count = int(state.get("retry_count", 0) or 0)
    terminal_failure_reason = ""

    running = next(
        (
            step
            for step in plan
            if step.get("status") == "running"
            and (not current_step_id or step.get("step_id") == current_step_id)
        ),
        None,
    )
    if running is not None:
        tool_failure = state.get("tool_loop_failure")
        report = _report_for_running_step(state, running)
        if tool_failure:
            running["status"] = "failed"
            running["result"] = dict(tool_failure)
            current_step_id = ""
            retry_count = 0
            terminal_failure_reason = str(
                tool_failure.get("message")
                or tool_failure.get("reason")
                or "Specialist 工具循环失败"
            )
            record_trace_event(
                state.get("trace_id"),
                "plan_step_failed",
                name="supervisor_agent",
                payload={
                    "step_id": running.get("step_id"),
                    "assigned_agent": running.get("assigned_agent"),
                    "reason": tool_failure.get("reason"),
                    "tool_call_count": tool_failure.get("tool_call_count"),
                },
            )
        elif report is not None:
            running["status"] = "completed"
            running["result"] = dict(report)
            current_step_id = ""
            retry_count = 0
            record_trace_event(
                state.get("trace_id"),
                "plan_step_completed",
                name="supervisor_agent",
                payload={
                    "step_id": running.get("step_id"),
                    "assigned_agent": running.get("assigned_agent"),
                    "report_id": report.get("report_id"),
                },
            )
        elif retry_count < MAX_STEP_RETRIES:
            retry_count += 1
            route = str(running.get("assigned_agent") or "")
            completed, remaining = _plan_views(plan)
            record_trace_event(
                state.get("trace_id"),
                "plan_step_retry",
                name="supervisor_agent",
                payload={
                    "step_id": running.get("step_id"),
                    "assigned_agent": route,
                    "retry_count": retry_count,
                },
            )
            return {
                "plan": plan,
                "current_step": running.get("step_id"),
                "completed_steps": completed,
                "remaining_steps": remaining,
                "retry_count": retry_count,
                "tool_call_count": int(state.get("tool_call_count", 0) or 0),
                "tool_loop_failure": None,
                "supervisor_route": route,
                "supervisor_reason": f"步骤 {running.get('step_id')} 未返回报告，执行第 {retry_count} 次重试",
                "supervisor_finalized": False,
            }
        else:
            running["status"] = "failed"
            running["result"] = {
                "error": "specialist_did_not_return_report",
                "retry_count": retry_count,
            }
            current_step_id = ""
            retry_count = 0
            record_trace_event(
                state.get("trace_id"),
                "plan_step_failed",
                name="supervisor_agent",
                payload={
                    "step_id": running.get("step_id"),
                    "assigned_agent": running.get("assigned_agent"),
                },
            )

    pending = next((step for step in plan if step.get("status") == "pending"), None)
    if pending is not None:
        assigned_agent = str(pending.get("assigned_agent") or "")
        if assigned_agent not in SPECIALIST_AGENTS:
            pending["status"] = "failed"
            pending["result"] = {"error": f"invalid assigned_agent: {assigned_agent}"}
            completed, remaining = _plan_views(plan)
            return {
                "plan": plan,
                "current_step": None,
                "completed_steps": completed,
                "remaining_steps": remaining,
                "retry_count": 0,
                "supervisor_route": "verify",
                "supervisor_reason": "计划包含无效的 Specialist Agent 分派，交由 Verifier 记录失败",
                "supervisor_finalized": False,
            }
        pending["status"] = "running"
        completed, remaining = _plan_views(plan)
        record_trace_event(
            state.get("trace_id"),
            "plan_step_started",
            name="supervisor_agent",
            payload={
                "step_id": pending.get("step_id"),
                "assigned_agent": assigned_agent,
                "description": pending.get("description"),
            },
        )
        return {
            "plan": plan,
            "current_step": pending.get("step_id"),
            "completed_steps": completed,
            "remaining_steps": remaining,
            "retry_count": 0,
            "tool_call_count": 0,
            "tool_loop_failure": None,
            "supervisor_route": assigned_agent,
            "supervisor_reason": f"执行计划步骤 {pending.get('step_id')}: {pending.get('description')}",
            "supervisor_finalized": False,
        }

    completed, remaining = _plan_views(plan)
    return {
        "plan": plan,
        "current_step": None,
        "completed_steps": completed,
        "remaining_steps": remaining,
        "retry_count": 0,
        "supervisor_route": "verify",
        "supervisor_reason": (
            f"计划步骤失败：{terminal_failure_reason}，进入结果核验"
            if terminal_failure_reason
            else "计划已无待执行步骤，进入结果核验"
        ),
        "supervisor_finalized": False,
    }


async def _llm_supervisor_direct_response(state: AgentState, reason: str) -> str:
    """Answer non-legal chitchat without performing specialist analysis."""
    latest_query = latest_human_message(state)
    fallback = "我在，你慢慢说。可以先告诉我发生了什么，或者你现在最想解决哪件事。"
    payload = {"用户输入": latest_query, "路由理由": reason}
    try:
        llm_factory = compatibility_dependency("get_llm", get_llm)
        llm = llm_factory(
            provider=os.getenv("SUPERVISOR_PROVIDER", "deepseek"),
            model=os.getenv("SUPERVISOR_MODEL", "deepseek-v4-flash-vision-exp"),
            model_route="supervisor_agent",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0.3,
            streaming=False,
        )
        response = await llm.ainvoke([
            SystemMessage(content=SUPERVISOR_DIRECT_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        return strip_answer_markdown((response.content or "").strip()) or fallback
    except Exception as exc:
        record_trace_event(
            state.get("trace_id"),
            "agent_fallback",
            name="supervisor_agent",
            payload={"error": str(exc)},
        )
        return fallback


async def supervisor_agent_node(state: AgentState) -> dict[str, Any]:
    """Route the initial request, then execute the Planner's steps one at a time."""
    if state.get("plan"):
        return _executor_update(state)

    if state.get("tool_loop_failure"):
        failure = state["tool_loop_failure"] or {}
        reason = str(failure.get("message") or "Specialist 工具调用达到上限")
        return {
            "supervisor_route": "end",
            "supervisor_reason": reason,
            "supervisor_finalized": True,
            "messages": [AIMessage(content=f"本次任务未能完成：{reason}。请缩小问题范围后重试。")],
        }

    reports = state.get("agent_reports", []) or []
    if reports:
        from agent.nodes.verifier import _llm_verifier_final_response

        final_content = await _llm_verifier_final_response(state)
        return {
            "supervisor_route": "end",
            "supervisor_reason": "兼容旧会话：专家报告已整理",
            "supervisor_finalized": True,
            "messages": [AIMessage(content=final_content)],
        }

    latest_query = latest_human_message(state)
    route_request = compatibility_dependency(
        "route_user_request_with_llm",
        route_user_request_with_llm,
    )
    decision = await route_request(
        message=latest_query,
        has_uploaded_doc=bool(state.get("uploaded_doc_text")),
        uploaded_doc_name=state.get("uploaded_doc_name"),
        trace_id=state.get("trace_id"),
        thread_id=state.get("thread_id"),
    )
    record_trace_event(
        state.get("trace_id"),
        "supervisor_route",
        name="supervisor_agent",
        payload={
            "route": decision.route,
            "reason": decision.reason,
            "complexity": decision.complexity,
            "need_tools": decision.need_tools,
        },
    )
    if decision.route == "final":
        final_content = await _llm_supervisor_direct_response(state, decision.reason)
        record_trace_event(
            state.get("trace_id"),
            "final_answer",
            name="supervisor_agent",
            payload={
                "content_preview": final_content[:500],
                "answer_score": score_legal_answer(latest_query, final_content, []),
            },
        )
        return {
            "intent": "non_legal",
            "intent_confidence": 0.0,
            "task_complexity": decision.complexity,
            "supervisor_route": "end",
            "supervisor_reason": decision.reason,
            "supervisor_finalized": True,
            "messages": [AIMessage(content=final_content)],
        }

    detected_intent = classify_legal_intent(latest_query)
    return {
        "intent": str(detected_intent["category"]),
        "intent_confidence": float(detected_intent["confidence"]),
        "task_complexity": decision.complexity,
        "supervisor_route": decision.route,
        "supervisor_reason": decision.reason,
        "supervisor_finalized": False,
    }
