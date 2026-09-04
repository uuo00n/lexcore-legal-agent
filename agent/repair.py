"""Repair Router 的确定性决策逻辑（§P0-5、P0-6）。

Repair Router 是普通 Node 而不是 Agent（§六）：它只按核验问题类型查表路由，
不调用模型、不产生新的法律结论。核心约束是「局部修复」——核验失败时只重跑受
影响的步骤，第一轮已核验的证据与无关步骤的报告必须原样保留（P0-6）。

本模块只依赖 ``agent.state`` 与 ``agent.agent_names``，便于单测直接验证路由表与计划重写。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from agent.agent_names import (
    AGENT_NODE_ALIASES,
    CASE_RETRIEVAL_AGENT,
    FACT_ANALYSIS_AGENT,
    LAW_RETRIEVAL_AGENT,
    LEGAL_REASONING_AGENT,
    agent_node,
)
from agent.state import AgentState, PlanStep, TaskType, VerificationResult

# §P0-5 修复路由表：核验问题类型 → 负责修复的执行单元（命名按 §四 的目标 Agent 名）。
REPAIR_ROUTING_MAP: dict[str, str] = {
    "citation_invalid": LAW_RETRIEVAL_AGENT,
    "retrieval_insufficient": LAW_RETRIEVAL_AGENT,
    "obsolete_law": LAW_RETRIEVAL_AGENT,
    # 语义核验给出的时效性风险与 obsolete_law 同源，同样只需重新检索现行有效条文；
    # 不入表会让它退回整体重排，正是 §二 问题 3 要消除的行为。
    "obsolete_law_risk": LAW_RETRIEVAL_AGENT,
    "case_evidence_insufficient": CASE_RETRIEVAL_AGENT,
    "reasoning_conflict": LEGAL_REASONING_AGENT,
    "overconfident": LEGAL_REASONING_AGENT,
    "answer_format_error": "answer_generator",
}

# 修复轮次预算；与 Replan 预算相互独立，避免「修复 → 核验」无限往复。
MAX_REPAIR_ROUNDS = 1

ANSWER_TARGET = "answer_generator"

# 修复目标缺少对应计划步骤时，用于补建步骤的任务类型。
_TARGET_TASK_TYPES: dict[str, TaskType] = {
    FACT_ANALYSIS_AGENT: TaskType.CASE_ANALYSIS,
    LAW_RETRIEVAL_AGENT: TaskType.STATUTE_RETRIEVAL,
    CASE_RETRIEVAL_AGENT: TaskType.CASE_RETRIEVAL,
    LEGAL_REASONING_AGENT: TaskType.LEGAL_CONSULTATION,
}

# 重开步骤时写进 description 的修复指令；该字段会进入模型上下文的 Current Plan 区块。
_REPAIR_INSTRUCTIONS: dict[str, str] = {
    FACT_ANALYSIS_AGENT: "重新梳理关键事实、法律关系与事实缺口，不得自行引用法条",
    LAW_RETRIEVAL_AGENT: "重新检索并核对被质疑的法条依据，只保留本轮检索到的现行有效条文",
    CASE_RETRIEVAL_AGENT: "补充检索与争议焦点匹配的裁判案例，并给出可核验的案号",
    LEGAL_REASONING_AGENT: "基于已核验证据重写法律分析，证据不足时不得给出确定性结论",
}


def repair_round_count(state: AgentState) -> int:
    """已执行的局部修复轮次；旧 checkpoint 缺该字段时按 0 处理。"""
    return max(0, int(state.get("repair_count") or 0))


def resolve_repair_node(target: str) -> str:
    """把 §四 的目标 Agent 名解析成当前图内的节点名。"""
    return agent_node(target)


def repair_target_for(issue: Mapping[str, object]) -> str:
    """按 §P0-5 路由表取修复目标；warning 只作风险提示，不触发修复。"""
    if issue.get("severity") == "warning":
        return ""
    return REPAIR_ROUTING_MAP.get(str(issue.get("type") or ""), "")


def repair_targets_for(issues: Iterable[Mapping[str, object]]) -> list[str]:
    """按问题出现顺序去重地收集修复目标，供 ``verification_result.repair_targets`` 使用。"""
    targets: list[str] = []
    for issue in issues or []:
        if not isinstance(issue, Mapping):
            continue
        target = repair_target_for(issue)
        if target and target not in targets:
            targets.append(target)
    return targets


def unroutable_issue_types(issues: Iterable[Mapping[str, object]]) -> list[str]:
    """列出没有修复目标的阻断问题类型；这类问题只能交给整体重排。"""
    unroutable: list[str] = []
    for issue in issues or []:
        if not isinstance(issue, Mapping) or issue.get("severity") == "warning":
            continue
        if repair_target_for(issue):
            continue
        issue_type = str(issue.get("type") or "unknown")
        if issue_type not in unroutable:
            unroutable.append(issue_type)
    return unroutable


@dataclass
class RepairPlan:
    """一次局部修复的完整决策。"""

    targets: list[str] = field(default_factory=list)
    plan: list[PlanStep] = field(default_factory=list)
    reopened: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    unroutable: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def answer_only(self) -> bool:
        """只需要重写答案：不重跑任何 Agent，直接回到 Answer Generator。"""
        return bool(self.targets) and not self.reopened

    @property
    def repairable(self) -> bool:
        return bool(self.targets)


def _issue_reason(issues: Sequence[Mapping[str, object]], target: str) -> str:
    """取该目标对应的首个问题描述，用于写进步骤 description 与 trace。"""
    for issue in issues:
        if repair_target_for(issue) == target:
            return str(issue.get("message") or issue.get("type") or "").strip()
    return ""


def _repair_description(target: str, reason: str) -> str:
    instruction = _REPAIR_INSTRUCTIONS.get(target, "按核验问题重新执行本步骤")
    text = f"{instruction}（修复原因：{reason}）" if reason else instruction
    return text[:200]


def _task_type_value(value: object) -> str:
    """把 ``TaskType`` 与旧 checkpoint 里的裸字符串统一成可比较的取值。"""
    return str(getattr(value, "value", value) or "")


def _step_matches(step: Mapping[str, object], target: str) -> bool:
    """判断某个计划步骤是否由该修复目标负责。

    §四 的四个执行单元现在各自对应一个图内节点，但旧 checkpoint 里的
    ``case_retrieval`` 步骤仍可能被分派到 ``case_analysis_agent``（当时类案检索挂在
    事实分析 Agent 上）。因此除节点名外还要比对 ``task_type``：否则「类案证据不足」
    会错误地重开事实分析步骤，并让事实 Agent 去检索类案。
    步骤未声明 ``task_type``（旧 checkpoint）时退回按节点名匹配。
    """
    if str(step.get("assigned_agent") or "") != resolve_repair_node(target):
        return False
    expected = _TARGET_TASK_TYPES.get(target)
    declared = step.get("task_type")
    if expected is None or declared is None:
        return True
    return _task_type_value(declared) == _task_type_value(expected)


def plan_repair(
    state: AgentState,
    verification: VerificationResult | Mapping[str, object] | None = None,
) -> RepairPlan:
    """把结构化核验问题转成最小修复计划：只重开受影响的步骤（P0-5、P0-6）。

    重开点取「命中目标 Agent 的最早步骤」，其后续步骤一并重开：下游步骤消费上游
    证据，只补检索而不重写分析会让同一条错误引用再次通过。更早的已完成步骤及其
    报告、以及本轮已归一化的证据全部保留。
    """
    verification = verification if isinstance(verification, Mapping) else (state.get("verification_result") or {})
    issues = [
        issue
        for issue in verification.get("structured_issues") or []  # type: ignore[union-attr]
        if isinstance(issue, Mapping)
    ]
    repair = RepairPlan(
        targets=repair_targets_for(issues),
        unroutable=unroutable_issue_types(issues),
    )
    plan: list[PlanStep] = [dict(step) for step in state.get("plan") or []]  # type: ignore[misc]
    repair.plan = plan
    if not repair.targets:
        return repair

    agent_targets = [target for target in repair.targets if target != ANSWER_TARGET]
    # 计划里没有对应步骤的修复目标先补建一步，并插到第一个法律分析步骤之前：
    # 新证据必须经过重新分析才有意义，插在末尾等于补了证据却不改结论。
    for target in agent_targets:
        if target not in _TARGET_TASK_TYPES:
            continue
        if any(_step_matches(step, target) for step in plan):
            continue
        step_id = f"repair_{len(repair.added) + 1}"
        insert_at = next(
            (
                index
                for index, step in enumerate(plan)
                if _step_matches(step, LEGAL_REASONING_AGENT)
            ),
            len(plan),
        )
        plan.insert(insert_at, {
            "step_id": step_id,
            "task_type": _TARGET_TASK_TYPES[target],
            "description": _repair_description(target, _issue_reason(issues, target)),
            "assigned_agent": resolve_repair_node(target),
            "status": "pending",
            "required": True,
        })
        repair.added.append(step_id)

    first = next(
        (
            index
            for index, step in enumerate(plan)
            if any(_step_matches(step, target) for target in agent_targets)
        ),
        None,
    )
    if first is not None:
        for step in plan[first:]:
            if step.get("required", True) is False and step.get("status") == "skipped":
                continue
            step_id = str(step.get("step_id") or "")
            target = next(
                (item for item in agent_targets if _step_matches(step, item)),
                "",
            )
            if target:
                step["description"] = _repair_description(target, _issue_reason(issues, target))
            step["status"] = "pending"
            step["result"] = None
            if step_id:
                repair.reopened.append(step_id)

    repair.reason = "；".join(
        filter(None, (_issue_reason(issues, target) for target in repair.targets))
    )[:300]
    return repair


__all__ = [
    "AGENT_NODE_ALIASES",
    "ANSWER_TARGET",
    "MAX_REPAIR_ROUNDS",
    "REPAIR_ROUTING_MAP",
    "RepairPlan",
    "plan_repair",
    "repair_round_count",
    "repair_target_for",
    "repair_targets_for",
    "resolve_repair_node",
    "unroutable_issue_types",
]
