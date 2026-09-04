"""Fact Merge 与 Clarification 节点（§七、§八、§十五）。

两个节点都是普通 Node（§六）：不调用模型，不产生法律结论。

- ``fact_merge_node``：每轮都跑。识别本轮是否是用户对上一轮补问的回复，把回复并入
  ``confirmed_facts``，并把「原始问题 + 用户补充」合并成本轮的实际问题。合并后由
  Fact Analysis 重新判定事实充分性——恢复后**不得**直接跳到 Planner（§八）。
- ``clarification_node``：把待补问的问题渲染成用户可读的追问并结束本轮。

澄清循环只处理「缺用户事实」；Agent 执行出错属于 Repair Loop（``agent.repair``），
两者不得混用。
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from agent.clarification import (
    MAX_CLARIFICATION_ROUNDS,
    clarification_round_count,
)
from agent.node_utils import latest_human_message, record_trace_event
from agent.state import AgentState
from services.workflow_metrics import record_clarification

# 已确认事实里保存原始问题的键名；澄清恢复时用它重建完整问题。
ORIGINAL_QUESTION_KEY = "原始问题"
_SUPPLEMENT_KEY = "用户补充"

_CLARIFICATION_INTRO = "为了给你准确的判断，我还需要确认几件事："
_CLARIFICATION_OUTRO = "你可以只回答你清楚的部分，我会基于你提供的信息继续分析。"
# 非阻断场景不会走到这个节点，这里只兜底极端情况：判定要补问却没有具体问题。
_FALLBACK_QUESTION = "请补充事情的经过、时间、涉及金额，以及你手上有哪些材料。"


def _pending_clarification(state: AgentState) -> bool:
    """上一轮是否以阻断式补问结束——只有这种情况才需要把本轮当作补充回答。"""
    return bool(state.get("needs_clarification")) and bool(state.get("clarification_blocking"))


def _merged_question(original: str, supplements: list[str], reply: str) -> str:
    parts = [part for part in [original.strip(), *supplements, reply.strip()] if part]
    return " ".join(parts)[:1000]


def _previous_supplements(confirmed: dict[str, Any]) -> list[str]:
    return [
        str(value).strip()
        for key, value in confirmed.items()
        if str(key).startswith(_SUPPLEMENT_KEY) and str(value).strip()
    ]


def fact_merge_node(state: AgentState) -> dict[str, Any]:
    """把用户对补问的回复并入已确认事实，并重建本轮的完整问题（§八）。"""
    if not _pending_clarification(state):
        # 普通轮次：只声明本轮不是澄清恢复，不触碰任何已确认事实。
        return {"clarification_resumed": False}

    reply = latest_human_message(state).strip()
    confirmed = dict(state.get("confirmed_facts") or {})
    original = str(confirmed.get(ORIGINAL_QUESTION_KEY) or "").strip()
    round_count = clarification_round_count(state)
    supplements = _previous_supplements(confirmed)
    merged_question = _merged_question(original, supplements, reply)

    record_trace_event(
        state.get("trace_id"),
        "clarification_resumed",
        name="fact_merge",
        payload={
            "clarification_round": round_count,
            "reply_chars": len(reply),
            "asked_questions": list(state.get("clarification_questions", []) or []),
        },
    )
    # §二十五：补问发起数与恢复数配对，才能算出「问了但用户没回」的流失比例。
    record_clarification("resumed")
    return {
        "clarification_resumed": True,
        # 空回复不得覆盖已确认事实，交给 reducer 处理（§八）。
        "confirmed_facts": {f"{_SUPPLEMENT_KEY}{round_count}": reply},
        "rewritten_query": merged_question or reply,
        # 清掉上一轮的补问状态，让 Fact Analysis 基于合并后的事实重新判定。
        "needs_clarification": False,
        "clarification_blocking": False,
        "clarification_questions": [],
        "needs_follow_up": False,
    }


def _render_clarification(state: AgentState) -> str:
    questions = [str(item).strip() for item in state.get("clarification_questions", []) or [] if str(item).strip()]
    if not questions:
        questions = [_FALLBACK_QUESTION]
    numbered = "\n".join(f"{index}. {question}" for index, question in enumerate(questions[:3], start=1))
    return f"{_CLARIFICATION_INTRO}\n{numbered}\n{_CLARIFICATION_OUTRO}"


def clarification_node(state: AgentState) -> dict[str, Any]:
    """向用户提出补问并结束本轮；不做任何法律判断，也不写入答案引用。"""
    question = str(state.get("rewritten_query") or "").strip() or latest_human_message(state).strip()
    content = _render_clarification(state)
    round_count = clarification_round_count(state) + 1
    confirmed = dict(state.get("confirmed_facts") or {})
    updates: dict[str, Any] = {}
    if not str(confirmed.get(ORIGINAL_QUESTION_KEY) or "").strip() and question:
        # 只在第一次补问时记住原始问题：第二轮的 latest message 是用户的补充回答，
        # 用它覆盖会让恢复时重建出错误的问题。
        updates[ORIGINAL_QUESTION_KEY] = question

    record_trace_event(
        state.get("trace_id"),
        "clarification_required",
        name="clarification",
        payload={
            "clarification_round": round_count,
            "max_rounds": MAX_CLARIFICATION_ROUNDS,
            "questions": list(state.get("clarification_questions", []) or []),
            "missing_facts": list(state.get("missing_facts", []) or []),
        },
    )
    # 走到本节点一定是阻断式补问（非阻断场景不进这里），标签固定为 blocking。
    record_clarification("required", blocking=True)
    result: dict[str, Any] = {
        "clarification_round": round_count,
        "needs_clarification": True,
        "clarification_blocking": True,
        "needs_follow_up": True,
        "supervisor_route": "end",
        "supervisor_reason": "关键事实不足，已向用户补问",
        "supervisor_finalized": True,
        "messages": [AIMessage(content=content)],
    }
    if updates:
        result["confirmed_facts"] = updates
    return result


__all__ = ["ORIGINAL_QUESTION_KEY", "clarification_node", "fact_merge_node"]
