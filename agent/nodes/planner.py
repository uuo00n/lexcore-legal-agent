"""Structured legal task planner node."""
from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.agent_names import same_agent
from agent.agents.fact_analysis_agent import case_facts_payload
from agent.node_utils import compatibility_dependency, latest_human_message, record_trace_event
from agent.prompts import PLANNER_SYSTEM_PROMPT
from agent.state import AgentState, PlanStep, TaskType
from services.legal_analysis import classify_legal_intent, is_legal_information_query
from services.llm import get_llm
from services.model_defaults import FAST, resolve_model, resolve_provider
from services.workflow_metrics import record_planner_degraded


MAX_PLAN_STEPS = 6
# 图内节点名与 §四 的规范职责名都允许出现在模型输出里；写进计划的取值由
# ``_normalize_steps`` 统一成 ``TASK_AGENT_MAP`` 的节点名。
AssignedAgent = Literal[
    "case_analysis_agent",
    "statute_retrieval_agent",
    "case_retrieval_agent",
    "legal_consult_agent",
    "fact_analysis_agent",
    "law_retrieval_agent",
    "legal_reasoning_agent",
]

TASK_AGENT_MAP: dict[TaskType, AssignedAgent] = {
    TaskType.CASE_ANALYSIS: "case_analysis_agent",
    TaskType.STATUTE_RETRIEVAL: "statute_retrieval_agent",
    # §五：类案检索有独立执行单元，不再挂在事实分析 Agent 上。
    TaskType.CASE_RETRIEVAL: "case_retrieval_agent",
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
        # 只要指向同一个执行单元就接受：模型可能写 law_retrieval_agent，也可能写
        # statute_retrieval_agent，两者是同一个节点（§四 兼容别名）。
        if not same_agent(self.assigned_agent, expected):
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
        # §五：类案检索只在 Complexity Router 判定需要时才安排，不作为默认步骤。
        if complexity == "high" and state.get("needs_case_retrieval"):
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
    allow_case_retrieval = bool(state.get("needs_case_retrieval"))
    for step in steps[:MAX_PLAN_STEPS]:
        if step.task_type in seen_task_types:
            continue
        if step.task_type == TaskType.CASE_RETRIEVAL and not allow_case_retrieval:
            # §五：类案检索只在明确需要时执行；这条约束由代码保证，不依赖模型自觉。
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
        "complexity_level": state.get("complexity_level") or "",
        # §五：类案检索按需执行，Complexity Router 说不需要时不得规划案例检索步骤。
        "needs_case_retrieval": bool(state.get("needs_case_retrieval")),
        "supervisor_route": state.get("supervisor_route") or "",
        "has_uploaded_document": bool(state.get("uploaded_doc_text")),
        # Fact Analysis Agent 已经整理过事实，Planner 不必再规划重复的事实收集步骤（§四）。
        "case_facts": case_facts_payload(state),
        "previous_plan": state.get("plan", []) or [],
        "verification_result": state.get("verification_result"),
        "replan_retry_count": int(state.get("replan_retry_count", 0) or 0),
    }

    degraded = False
    try:
        llm_factory = compatibility_dependency("get_llm", get_llm)
        llm = llm_factory(
            # 计划本身只是结构化的步骤清单，不需要最强模型；走 fast 档省 token（§P1-5 兜底仍在）。
            provider=resolve_provider("PLANNER_PROVIDER", "SUPERVISOR_PROVIDER", tier=FAST),
            model=resolve_model("PLANNER_MODEL", "SUPERVISOR_MODEL", tier=FAST),
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
        # §P1-5：兜底计划保留，但必须标记降级——Provider 报错不能变成一条静默的
        # 「看起来正常」的计划。两个事件分别给通用兜底监控与 planner 专属看板用。
        record_trace_event(
            state.get("trace_id"),
            "agent_fallback",
            name="planner",
            payload={"error": str(exc)},
        )
        record_trace_event(
            state.get("trace_id"),
            "planner_degraded",
            name="planner",
            payload={"reason": "planner_llm_unavailable", "error": str(exc)[:300]},
        )
        steps = _fallback_steps(state)
        degraded = True
        record_planner_degraded()

    plan = _normalize_steps(steps, state=state, query=query)
    if not plan:
        plan = _normalize_steps(_fallback_steps(state), state=state, query=query)
    record_trace_event(
        state.get("trace_id"),
        "plan_created",
        name="planner",
        payload={"intent": intent, "step_count": len(plan), "planner_degraded": degraded},
    )
    return {
        "plan": plan,
        "remaining_steps": [dict(step) for step in plan],
        # 成功规划显式写回 False：一次降级不该让后续重排也一直挂着降级标记。
        "planner_degraded": degraded,
    }
