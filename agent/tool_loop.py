"""Shared guardrails for bounded Specialist ReAct tool loops.

三个 Specialist 内部都是 ReAct 微循环（§三），本模块是它们共用的刹车：

- 硬上限：单个 Agent 任务最多 ``MAX_TOOL_CALLS_PER_AGENT`` 次工具调用（§P1-2）。
  超限仍按 ``ToolLoopFailure`` 交回 Supervisor 记为步骤失败，保持既有语义。
- 全请求预算：一次问答里所有 Agent、所有计划步骤、所有修复轮累计最多
  ``MAX_TOOL_CALLS_PER_REQUEST`` 次工具调用。单任务上限会在每次分派计划步骤、
  每轮局部修复和整体重排时归零，因此它管不住一次请求的总成本；这一层按软停止
  处理，耗尽后 Agent 用已有证据出报告，不判步骤失败。
- 软停止（§P1-2 其余三个停止条件）：目标证据量已达成、相同 ``query_signature``
  重复调用（§二十二）、上一轮工具观测没带来新证据（``evidence_gain <= 阈值``，
  §P1-3）。软停止不判失败——Agent 直接用手上的证据写报告，这才是「让 Agent 更少
  地做无意义工作」。

软停止只在 Repair Router 要求刷新时让路（§二十二）：局部修复本身就是为了重新
检索被质疑的条文，此时 ``tool_refresh_allowed`` 为真，前两个软停止不生效；
``evidence_gain`` 停止条件继续保留——刷新后仍然没有新证据就没有再查的意义。
全请求预算不让路：刷新可以绕过「证据够用」和「重复签名」，但绕不过成本上限，
否则预算会从修复路径漏出去。
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from agent.node_utils import record_trace_event
from agent.state import AgentState, ToolLoopFailure
from services.cache.keys import digest, normalize_text
from services.errors import ToolError
from services.retry import is_retryable_exception
from services.workflow_metrics import record_tool_calls, record_tool_loop_stopped


def _positive_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


# §P1-2：单个 Agent 任务的工具调用上限。``MAX_TOOL_CALLS`` 保留为历史别名，
# 既有导入方（agent.nodes 门面、routing 日志）无需改动。
MAX_TOOL_CALLS_PER_AGENT = _positive_env(
    "MAX_TOOL_CALLS_PER_AGENT",
    _positive_env("MAX_TOOL_CALLS", 2),
)
MAX_TOOL_CALLS = MAX_TOOL_CALLS_PER_AGENT
# 单任务上限之上再加一层：一次请求内累计放行的工具调用总数。单任务计数会在分派计划
# 步骤（Supervisor）、局部修复（Repair Router）和整体重排（Verifier）时归零，只有这个
# 累计值按请求存活，所以它才是真正的成本闸门。
MAX_TOOL_CALLS_PER_REQUEST = _positive_env("MAX_TOOL_CALLS_PER_REQUEST", 3)
TOOL_CALL_LIMIT_ERROR = "tool_call_limit_exceeded"

# 目标证据量：达到即停止继续检索，与 §P1-7「只送 Top 5~8 条证据进模型」对齐。
EVIDENCE_LAW_TARGET = _positive_env("EVIDENCE_LAW_TARGET", 5)
EVIDENCE_CASE_TARGET = _positive_env("EVIDENCE_CASE_TARGET", 3)
# §P1-3：新增证据不超过该阈值就立即停止；0 表示「一条都没新增」。
try:
    EVIDENCE_GAIN_STOP_THRESHOLD = max(0, int(os.getenv("EVIDENCE_GAIN_STOP_THRESHOLD", "0")))
except (TypeError, ValueError):
    EVIDENCE_GAIN_STOP_THRESHOLD = 0

STOP_EVIDENCE_TARGET_REACHED = "evidence_target_reached"
STOP_DUPLICATE_QUERY = "duplicate_query"
STOP_NO_EVIDENCE_GAIN = "no_evidence_gain"
STOP_REQUEST_BUDGET_EXHAUSTED = "request_budget_exhausted"

_LAW_TOOLS = frozenset({"search_law_tool", "retrieve_local_law_tool"})
_CASE_TOOLS = frozenset({"search_case_tool"})
# 参与 query_signature 关键词部分的参数；其余非空参数算过滤条件。
_KEYWORD_ARG_KEYS = ("query", "keywords", "keyword", "long_text", "question", "text")
# 运行时注入项不代表检索意图，不能进签名。
_IGNORED_ARG_KEYS = frozenset({"trace_id", "config", "callbacks", "run_manager", "state"})
_TOKEN_SPLIT_RE = re.compile(r"[\s,，、;；。.!！?？:：\"'“”‘’()（）\[\]【】{}/\\|+_-]+")


def _replace_tool_calls(response: AIMessage, calls: list[dict[str, Any]]) -> AIMessage:
    """Keep the model response while admitting only calls that fit the budget."""
    try:
        response.tool_calls = calls
        return response
    except Exception:
        return AIMessage(
            content=response.content or "",
            tool_calls=calls,
            additional_kwargs=response.additional_kwargs,
            response_metadata=response.response_metadata,
            id=response.id,
            name=response.name,
        )


def _normalized_keywords(args: Mapping[str, Any]) -> str:
    """把检索关键词收敛成与措辞顺序无关的 token 集合。"""
    parts: list[str] = []
    for key in _KEYWORD_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            parts.extend(str(item) for item in value)
        elif value is not None:
            parts.append(str(value))
    tokens = {
        token
        for part in parts
        for token in _TOKEN_SPLIT_RE.split(normalize_text(part).lower())
        if token
    }
    return " ".join(sorted(tokens))


def _normalized_filters(args: Mapping[str, Any]) -> str:
    """过滤条件按键排序参与签名：TopK、分页、排序不同即视为不同调用。"""
    items = sorted(
        (str(key), str(value))
        for key, value in args.items()
        if key not in _KEYWORD_ARG_KEYS
        and key not in _IGNORED_ARG_KEYS
        and value not in (None, "", [], {}, ())
    )
    return ";".join(f"{key}={value}" for key, value in items)


def query_signature(tool_name: str | None, args: Any = None) -> str:
    """§二十二：``hash(tool_name + 归一化关键词 + 过滤条件)``。

    摘要复用 ``services.cache.keys.digest``：检索关键词可能包含用户案情，签名只
    保留 sha256 摘要，不把明文带进 State 与 Trace。
    """
    mapping = args if isinstance(args, Mapping) else {}
    return digest(
        str(tool_name or "").strip().lower(),
        _normalized_keywords(mapping),
        _normalized_filters(mapping),
    )


def tool_call_signature(call: Mapping[str, Any]) -> str:
    """取单个模型工具调用的 ``query_signature``。"""
    return query_signature(call.get("name"), call.get("args"))


def _target_reached(call: Mapping[str, Any], law_count: int, case_count: int) -> bool:
    """该调用想补的证据是否已经到量。"""
    name = str(call.get("name") or "")
    if name in _LAW_TOOLS:
        return law_count >= EVIDENCE_LAW_TARGET
    if name in _CASE_TOOLS:
        return case_count >= EVIDENCE_CASE_TARGET
    return False


@dataclass(frozen=True)
class ToolStopDecision:
    """一次工具循环的软停止判定结果。"""

    stop: bool = False
    reason: str = ""
    admitted: tuple[dict[str, Any], ...] = ()
    duplicates: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)


def recorded_query_signatures(state: AgentState) -> list[str]:
    """本轮已经执行过的检索签名；旧 checkpoint 缺该字段时按空处理。"""
    return [str(item) for item in (state.get("tool_query_signatures") or []) if item]


def request_tool_call_total(state: AgentState) -> int:
    """本次请求已累计放行的工具调用次数；旧 checkpoint 缺该字段时按 0 处理。"""
    return max(0, int(state.get("tool_call_total") or 0))


def request_budget_exhausted(state: AgentState) -> bool:
    """全请求工具预算是否已用尽。

    用尽之后任何「重开步骤 → 重新检索」的重跑都拿不到新证据，调用方应当直接走
    基于已核验证据作答的分支，而不是再烧一轮 Specialist + Verifier。
    """
    return request_tool_call_total(state) >= MAX_TOOL_CALLS_PER_REQUEST


def evaluate_tool_stop(state: AgentState, calls: Sequence[Mapping[str, Any]]) -> ToolStopDecision:
    """按 §P1-2 的软停止条件判断这批工具调用还该不该发出去。

    命中顺序：全请求预算 → 目标证据量 → ``evidence_gain`` → 重复签名。全请求预算
    排在最前面，因为 ``tool_refresh_allowed`` 会让后面几个条件整段让路，而成本上限
    不能让路。``evidence_gain`` 只在本任务已经检索过至少一次（``tool_call_count > 0``）
    时才参与判断，否则刚接手步骤的 Agent 会被上一步的陈旧增益直接掐掉第一次检索。
    """
    admitted = [dict(call) for call in calls]
    if not admitted:
        return ToolStopDecision(admitted=())

    count = max(0, int(state.get("tool_call_count") or 0))
    total = request_tool_call_total(state)
    law_count = len(state.get("retrieved_laws") or [])
    case_count = len(state.get("retrieved_cases") or [])
    refresh = bool(state.get("tool_refresh_allowed"))

    if total >= MAX_TOOL_CALLS_PER_REQUEST:
        return ToolStopDecision(
            stop=True,
            reason=STOP_REQUEST_BUDGET_EXHAUSTED,
            detail={
                "tool_call_total": total,
                "max_tool_calls_per_request": MAX_TOOL_CALLS_PER_REQUEST,
            },
        )

    if not refresh and all(_target_reached(call, law_count, case_count) for call in admitted):
        return ToolStopDecision(
            stop=True,
            reason=STOP_EVIDENCE_TARGET_REACHED,
            detail={"law_count": law_count, "case_count": case_count},
        )

    gain = state.get("evidence_gain")
    if count > 0 and gain is not None and int(gain or 0) <= EVIDENCE_GAIN_STOP_THRESHOLD:
        return ToolStopDecision(
            stop=True,
            reason=STOP_NO_EVIDENCE_GAIN,
            detail={"evidence_gain": int(gain or 0), "tool_call_count": count},
        )

    if refresh:
        return ToolStopDecision(admitted=tuple(admitted))

    seen = set(recorded_query_signatures(state))
    fresh: list[dict[str, Any]] = []
    duplicates: list[str] = []
    for call in admitted:
        signature = tool_call_signature(call)
        if signature in seen:
            duplicates.append(str(call.get("name") or ""))
            continue
        seen.add(signature)
        fresh.append(call)
    if not fresh:
        return ToolStopDecision(
            stop=True,
            reason=STOP_DUPLICATE_QUERY,
            duplicates=tuple(duplicates),
            detail={"duplicate_tools": duplicates},
        )
    return ToolStopDecision(admitted=tuple(fresh), duplicates=tuple(duplicates))


def apply_tool_call_budget(
    response: AIMessage,
    state: AgentState,
    *,
    agent_name: str,
) -> tuple[AIMessage, int, int, ToolLoopFailure | None]:
    """Admit tool calls up to the per-task and per-request budgets.

    返回 ``(响应, 单任务累计, 全请求累计, 失败信息)``。``ToolLoopFailure`` 只由单任务
    硬上限产生；全请求预算耗尽时把这批调用全部裁掉且不判失败，交由调用方按软停止处理。
    """
    calls = list(getattr(response, "tool_calls", None) or [])
    current_count = max(0, int(state.get("tool_call_count", 0) or 0))
    current_total = request_tool_call_total(state)
    if not calls:
        return response, current_count, current_total, None

    remaining = max(
        0,
        min(
            MAX_TOOL_CALLS_PER_AGENT - current_count,
            MAX_TOOL_CALLS_PER_REQUEST - current_total,
        ),
    )
    if remaining:
        admitted = calls[:remaining]
        response = _replace_tool_calls(response, admitted)
        granted = len(admitted)
        return response, current_count + granted, current_total + granted, None

    if current_count < MAX_TOOL_CALLS_PER_AGENT:
        # 单任务还有额度，卡住的是全请求预算：软停止语义，不产出 ToolLoopFailure。
        # ``evaluate_tool_stop`` 正常会先一步拦停，这里是防御性兜底。
        return _replace_tool_calls(response, []), current_count, current_total, None

    failure: ToolLoopFailure = {
        "agent_name": agent_name,
        "task_id": str(
            state.get("current_step")
            or state.get("trace_id")
            or f"current-request:{agent_name}"
        ),
        "reason": TOOL_CALL_LIMIT_ERROR,
        "message": f"任务工具调用次数已达到上限 {MAX_TOOL_CALLS_PER_AGENT}，拒绝继续调用工具",
        "tool_call_count": current_count,
        "max_tool_calls": MAX_TOOL_CALLS_PER_AGENT,
        "requested_tools": [str(call.get("name") or "") for call in calls],
    }
    return response, current_count, current_total, failure


@dataclass(frozen=True)
class ToolLoopStep:
    """Specialist 拿到模型响应后该怎么走。

    ``updates`` 非空表示继续工具循环，把它写回 State 即可；``updates`` 为 ``None``
    表示本轮不再调用工具，Agent 应当直接用手上的证据写报告。
    """

    updates: dict[str, Any] | None = None
    stop_reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def continue_loop(self) -> bool:
        return self.updates is not None


def _soft_stop(
    state: AgentState,
    *,
    agent_name: str,
    reason: str,
    requested_tools: list[str],
    detail: dict[str, Any],
) -> ToolLoopStep:
    """记录一次软停止并告诉 Specialist「别再调工具了，用手上的证据写报告」。"""
    record_trace_event(
        state.get("trace_id"),
        "tool_loop_stopped",
        name=agent_name,
        payload={
            "reason": reason,
            "requested_tools": requested_tools,
            "tool_call_count": max(0, int(state.get("tool_call_count") or 0)),
            "tool_call_total": request_tool_call_total(state),
            **detail,
        },
    )
    # §二十五：软停止原因分布是「少做无意义检索」的直接证据。
    record_tool_loop_stopped(agent_name, reason)
    return ToolLoopStep(stop_reason=reason, detail=dict(detail))


def admit_tool_calls(
    response: AIMessage,
    state: AgentState,
    *,
    agent_name: str,
) -> ToolLoopStep:
    """把「要不要继续调工具」收敛成一个入口（§P1-2、§P1-3、§二十二）。

    软停止不写 ``tool_loop_failure``：证据够用、重复检索、零增益、全请求预算耗尽都不是
    执行错误，Agent 用已有证据写报告即可；只有单任务硬上限仍按既有语义交回 Supervisor
    判失败。
    """
    calls = list(getattr(response, "tool_calls", None) or [])
    if not calls:
        return ToolLoopStep()

    requested_tools = [str(call.get("name") or "") for call in calls]
    decision = evaluate_tool_stop(state, calls)
    if decision.stop:
        return _soft_stop(
            state,
            agent_name=agent_name,
            reason=decision.reason,
            requested_tools=requested_tools,
            detail=dict(decision.detail),
        )

    admitted = list(decision.admitted)
    if len(admitted) != len(calls):
        response = _replace_tool_calls(response, admitted)
    response, tool_call_count, tool_call_total, failure = apply_tool_call_budget(
        response,
        state,
        agent_name=agent_name,
    )
    signatures = recorded_query_signatures(state)
    granted = list(getattr(response, "tool_calls", None) or [])
    if not granted and failure is None:
        # 全请求预算把整批调用裁空（evaluate_tool_stop 之后的兜底路径）：按软停止收尾，
        # 否则会把一条没有 tool_calls 的响应塞进历史、让本步骤既不检索也不出报告。
        return _soft_stop(
            state,
            agent_name=agent_name,
            reason=STOP_REQUEST_BUDGET_EXHAUSTED,
            requested_tools=requested_tools,
            detail={
                "tool_call_total": tool_call_total,
                "max_tool_calls_per_request": MAX_TOOL_CALLS_PER_REQUEST,
            },
        )
    for call in granted:
        signature = tool_call_signature(call)
        if signature not in signatures:
            signatures.append(signature)
    # 只统计真正放行的调用数（预算裁剪之后），否则平均调用次数会被模型的过量请求抬高。
    record_tool_calls(agent_name, len(granted))
    return ToolLoopStep(
        updates={
            "messages": [response],
            "tool_call_count": tool_call_count,
            "tool_call_total": tool_call_total,
            "tool_loop_failure": failure,
            "tool_query_signatures": signatures,
        },
        detail={"duplicate_tools": list(decision.duplicates)},
    )


def tool_limit_observation_node(state: AgentState) -> dict[str, Any]:
    """Reject over-budget calls with protocol-valid error observations."""
    messages = list(state.get("messages", []) or [])
    last = messages[-1] if messages else None
    calls = list(getattr(last, "tool_calls", None) or [])
    failure = dict(state.get("tool_loop_failure") or {})
    failure.setdefault("reason", TOOL_CALL_LIMIT_ERROR)
    failure.setdefault("message", f"任务工具调用次数已达到上限 {MAX_TOOL_CALLS_PER_AGENT}")
    failure.setdefault("tool_call_count", int(state.get("tool_call_count", 0) or 0))
    failure.setdefault("max_tool_calls", MAX_TOOL_CALLS_PER_AGENT)
    failure.setdefault("requested_tools", [str(call.get("name") or "") for call in calls])

    observations = [
        ToolMessage(
            content=json.dumps(
                {
                    "status": "error",
                    "error": TOOL_CALL_LIMIT_ERROR,
                    "message": failure["message"],
                    "tool_call_count": failure["tool_call_count"],
                    "max_tool_calls": failure["max_tool_calls"],
                    "retryable": False,
                },
                ensure_ascii=False,
            ),
            tool_call_id=str(call.get("id") or f"rejected_tool_call_{index}"),
            name=str(call.get("name") or "unknown_tool"),
            status="error",
        )
        for index, call in enumerate(calls)
    ]
    result: dict[str, Any] = {"tool_loop_failure": failure}
    if observations:
        result["messages"] = observations
    return result


def tool_error_observation(error: Exception) -> str:
    """Render execution failures as observations the Specialist can react to."""
    normalized = error if isinstance(error, ToolError) else ToolError(
        str(error),
        retryable=is_retryable_exception(error),
    )
    instruction = (
        "这是临时故障；可稍后重试该工具，或改用其他可信来源。"
        if normalized.retryable
        else "不要重复提交相同调用；请修正参数、改用其他可信来源，或报告证据不足。"
    )
    return json.dumps(
        {
            "status": "error",
            "error": "tool_execution_error",
            "error_type": type(error).__name__,
            "normalized_error_type": type(normalized).__name__,
            "error_code": normalized.code,
            "message": str(normalized),
            "retryable": normalized.retryable,
            "instruction": instruction,
        },
        ensure_ascii=False,
    )
