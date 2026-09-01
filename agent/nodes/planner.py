"""Structured legal task planner node."""
from __future__ import annotations

import json
import os
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.node_utils import compatibility_dependency, latest_human_message, record_trace_event
from agent.prompts import PLANNER_SYSTEM_PROMPT
from agent.state import AgentState, PlanStep, TaskType
from services.legal_analysis import classify_legal_intent, is_legal_information_query
from services.llm import get_llm


MAX_PLAN_STEPS = 6
AssignedAgent = Literal[
    "case_analysis_agent",
    "statute_retrieval_agent",
    "legal_consult_agent",
]

TASK_AGENT_MAP: dict[TaskType, AssignedAgent] = {
    TaskType.CASE_ANALYSIS: "case_analysis_agent",
    TaskType.STATUTE_RETRIEVAL: "statute_retrieval_agent",
    TaskType.CASE_RETRIEVAL: "case_analysis_agent",
    TaskType.LEGAL_CONSULTATION: "legal_consult_agent",
}

NON_LEGAL_INTENTS = {
    "chat",
    "chitchat",
    "non_legal",
    "non-legal",
    "nonlegal",
    "闲聊",
    "非法律",
}


class PlannerStep(BaseModel):
    """LLM 生成的单个可执行计划步骤。"""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(pattern=r"^step_[1-6]$")
    task_type: TaskType
    description: str = Field(min_length=2, max_length=200)
    assigned_agent: AssignedAgent
    status: Literal["pending"] = "pending"
    required: bool = True

    @model_validator(mode="after")
    def validate_assignment(self) -> "PlannerStep":
        expected = TASK_AGENT_MAP[self.task_type]
        if self.assigned_agent != expected:
            raise ValueError(
                f"{self.task_type.value} must be assigned to {expected}"
            )
        return self


class PlannerOutput(BaseModel):
    """Planner 的结构化输出，硬性限制计划规模与重复步骤。"""

    model_config = ConfigDict(extra="forbid")

    steps: list[PlannerStep] = Field(min_length=1, max_length=MAX_PLAN_STEPS)

    @model_validator(mode="after")
    def reject_loop_like_steps(self) -> "PlannerOutput":
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("plan step_id values must be unique")

        signatures = [
            (step.task_type, "".join(step.description.lower().split()))
            for step in self.steps
        ]
        if len(signatures) != len(set(signatures)):
            raise ValueError("plan must not contain repeated steps")
        return self


def _intent_for_state(state: AgentState, query: str) -> tuple[str, float, bool]:
    """读取 State intent；缺失时仅作确定性兼容识别。"""
    state_intent = str(state.get("intent") or "").strip()
    if state_intent:
        is_legal = state_intent.lower() not in NON_LEGAL_INTENTS
        return state_intent, float(state.get("intent_confidence") or 0.0), is_legal

    detected = classify_legal_intent(query)
    if detected["is_legal"]:
        return str(detected["category"]), float(detected["confidence"]), True

    # 兼容只返回旧版路由字段的 Supervisor；正式链路会显式写入 intent。
    route = str(state.get("supervisor_route") or "")
    if route in {
        "case_analysis_agent",
        "statute_retrieval_agent",
        "legal_consult_agent",
    }:
        return route.removesuffix("_agent"), 0.0, True
    return "non_legal", 0.0, False


def _fallback_steps(state: AgentState) -> list[PlannerStep]:
    """模型不可用时生成最小、确定且不会循环的执行计划。"""
    route = state.get("supervisor_route")
    complexity = state.get("task_complexity") or "low"

    if route == "statute_retrieval_agent":
        tasks = [(TaskType.STATUTE_RETRIEVAL, "检索与问题直接相关的现行法律依据")]
    elif route == "legal_consult_agent":
        tasks = [(TaskType.LEGAL_CONSULTATION, "解释适用规则并给出可执行的法律建议")]
    elif complexity == "low":
        tasks = [(TaskType.CASE_ANALYSIS, "提取关键事实、法律关系和待确认信息")]
    else:
        tasks = [
            (TaskType.CASE_ANALYSIS, "提取关键事实、法律关系、争议焦点和证据缺口"),
            (TaskType.STATUTE_RETRIEVAL, "检索争议焦点对应的现行法律依据"),
        ]
        if complexity == "high":
            tasks.append((TaskType.CASE_RETRIEVAL, "检索争议焦点和事实结构相近的裁判案例"))
        tasks.append((TaskType.LEGAL_CONSULTATION, "综合事实与法律依据形成可执行建议"))

    return [
        PlannerStep(
            step_id=f"step_{index}",
            task_type=task_type,
            description=description,
            assigned_agent=TASK_AGENT_MAP[task_type],
        )
        for index, (task_type, description) in enumerate(tasks, start=1)
    ]


def _normalize_steps(
    steps: list[PlannerStep],
    *,
    state: AgentState,
    query: str,
) -> list[PlanStep]:
    """固定编号与分派，并删除可形成重复执行环的同类重复步骤。"""
    route = state.get("supervisor_route")
    complexity = state.get("task_complexity") or "low"
    if route == "statute_retrieval_agent" and (
        complexity == "low" or is_legal_information_query(query)
    ):
        statute_step = next(
            (step for step in steps if step.task_type == TaskType.STATUTE_RETRIEVAL),
            _fallback_steps(state)[0],
        )
        steps = [statute_step]

    normalized: list[PlanStep] = []
    seen_task_types: set[TaskType] = set()
    for step in steps[:MAX_PLAN_STEPS]:
        if step.task_type in seen_task_types:
            continue
        seen_task_types.add(step.task_type)
        normalized.append({
            "step_id": f"step_{len(normalized) + 1}",
            "task_type": step.task_type,
            "description": step.description.strip(),
            "assigned_agent": TASK_AGENT_MAP[step.task_type],
            "status": "pending",
            "required": step.required,
        })
    return normalized


async def planner_node(state: AgentState) -> dict[str, Any]:
    """把法律任务拆成最多六个步骤；该节点不会绑定或调用任何工具。"""
    query = (
        state.get("rewritten_query")
        or state.get("original_query")
        or latest_human_message(state)
    )
    intent, intent_confidence, is_legal = _intent_for_state(state, query)
    if not is_legal or state.get("supervisor_route") in {"end", "final"}:
        return {"plan": [], "remaining_steps": []}

    payload = {
        "query": query,
        "intent": intent,
        "intent_confidence": intent_confidence,
        "complexity": state.get("task_complexity") or "low",
        "supervisor_route": state.get("supervisor_route") or "",
        "has_uploaded_document": bool(state.get("uploaded_doc_text")),
        "previous_plan": state.get("plan", []) or [],
        "verification_result": state.get("verification_result"),
        "replan_retry_count": int(state.get("replan_retry_count", 0) or 0),
    }

    try:
        llm_factory = compatibility_dependency("get_llm", get_llm)
        llm = llm_factory(
            provider=os.getenv("PLANNER_PROVIDER", os.getenv("SUPERVISOR_PROVIDER", "deepseek")),
            model=os.getenv("PLANNER_MODEL", os.getenv("SUPERVISOR_MODEL", "deepseek-v4-flash-vision-exp")),
            model_route="planner",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0,
            streaming=False,
        )
        structured_llm = llm.with_structured_output(PlannerOutput)
        raw_output = await structured_llm.ainvoke([
            SystemMessage(content=PLANNER_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        output = raw_output if isinstance(raw_output, PlannerOutput) else PlannerOutput.model_validate(raw_output)
        steps = output.steps
    except Exception as exc:
        record_trace_event(
            state.get("trace_id"),
            "agent_fallback",
            name="planner",
            payload={"error": str(exc)},
        )
        steps = _fallback_steps(state)

    plan = _normalize_steps(steps, state=state, query=query)
    if not plan:
        plan = _normalize_steps(_fallback_steps(state), state=state, query=query)
    record_trace_event(
        state.get("trace_id"),
        "plan_created",
        name="planner",
        payload={"intent": intent, "step_count": len(plan)},
    )
    return {
        "plan": plan,
        "remaining_steps": [dict(step) for step in plan],
    }
