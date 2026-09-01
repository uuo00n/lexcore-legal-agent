"""Build bounded model context from the agent's layered memory and working state."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage

from services.memory import estimate_tokens


def _positive_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ContextBudget:
    """Input allocation. Output reserve is never made available to prompt content."""

    input_tokens: int = field(default_factory=lambda: _positive_env("CONTEXT_INPUT_TOKEN_BUDGET", 12000))
    output_reserve: int = field(default_factory=lambda: _positive_env("CONTEXT_OUTPUT_TOKEN_RESERVE", 2000))
    system: int = field(default_factory=lambda: _positive_env("CONTEXT_SYSTEM_TOKEN_BUDGET", 1800))
    relevant_memory: int = field(default_factory=lambda: _positive_env("CONTEXT_MEMORY_TOKEN_BUDGET", 900))
    summary: int = field(default_factory=lambda: _positive_env("CONTEXT_SUMMARY_TOKEN_BUDGET", 700))
    recent_messages: int = field(default_factory=lambda: _positive_env("CONTEXT_RECENT_MESSAGES_TOKEN_BUDGET", 2600))
    current_plan: int = field(default_factory=lambda: _positive_env("CONTEXT_PLAN_TOKEN_BUDGET", 700))
    evidence: int = field(default_factory=lambda: _positive_env("CONTEXT_EVIDENCE_TOKEN_BUDGET", 2400))
    current_task: int = field(default_factory=lambda: _positive_env("CONTEXT_CURRENT_TASK_TOKEN_BUDGET", 900))
    tool_result: int = field(default_factory=lambda: _positive_env("CONTEXT_TOOL_RESULT_TOKEN_BUDGET", 500))
    max_recent_messages: int = field(default_factory=lambda: _positive_env("CONTEXT_RECENT_MESSAGE_COUNT", 12))
    law_top_n: int = field(default_factory=lambda: _positive_env("CONTEXT_RETRIEVED_LAW_TOP_N", 6))
    case_top_n: int = field(default_factory=lambda: _positive_env("CONTEXT_RETRIEVED_CASE_TOP_N", 4))

    @property
    def prompt_tokens(self) -> int:
        return max(1, self.input_tokens - self.output_reserve)


@dataclass(frozen=True)
class BuiltContext:
    """One model invocation's bounded messages and auditable allocation status."""

    messages: list[BaseMessage]
    system_prompt: str
    selected_laws: list[dict[str, Any]]
    selected_cases: list[dict[str, Any]]
    status: dict[str, Any]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _truncate(text: str, token_limit: int) -> str:
    """Conservatively fit text into the project's Chinese-oriented token estimate."""
    value = text.strip()
    if not value or token_limit <= 0 or estimate_tokens(value) <= token_limit:
        return value
    suffix = "\n[已按上下文预算截断]"
    suffix_tokens = estimate_tokens(suffix)
    if token_limit <= suffix_tokens:
        max_chars = max(1, int(token_limit * 1.5))
        return value[:max_chars]
    low, high = 0, len(value)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(value[:mid]) <= token_limit - suffix_tokens:
            low = mid
        else:
            high = mid - 1
    return f"{value[:low].rstrip()}{suffix}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _score(item: Mapping[str, Any]) -> float:
    for key in ("rerank_score", "relevance_score", "score", "similarity", "final_score"):
        try:
            if item.get(key) is not None:
                return float(item[key])
        except (TypeError, ValueError):
            continue
    return 0.0


def select_top_evidence(items: Sequence[Mapping[str, Any]] | None, top_n: int) -> list[dict[str, Any]]:
    """Select a stable Top-N, preferring explicit retrieval/rerank scores when present."""
    indexed = [(index, dict(item)) for index, item in enumerate(items or [])]
    indexed.sort(key=lambda pair: (-_score(pair[1]), pair[0]))
    return [item for _, item in indexed[: max(0, top_n)]]


def _compact_value(value: Any, *, depth: int = 0) -> Any:
    """Create a deterministic semantic sketch of large structured tool output."""
    if depth >= 3:
        return _truncate(_text(value), 80)
    if isinstance(value, Mapping):
        priority = (
            "status", "source_type", "evidence_insufficient", "result_count", "count",
            "total", "query", "law_name", "article_no", "case_name", "case_no",
            "title", "summary", "content", "score", "relevance_score", "url",
        )
        keys = list(dict.fromkeys([key for key in priority if key in value] + list(value.keys())))[:16]
        return {str(key): _compact_value(value[key], depth=depth + 1) for key in keys}
    if isinstance(value, (list, tuple)):
        return {
            "count": len(value),
            "top_items": [_compact_value(item, depth=depth + 1) for item in value[:3]],
        }
    if isinstance(value, str):
        return _truncate(value, 120)
    return value


def summarize_tool_content(content: Any, token_limit: int) -> str:
    """Summarize oversized tool observations before they enter a model context."""
    raw = _text(content)
    if estimate_tokens(raw) <= token_limit:
        return raw
    try:
        parsed = json.loads(raw) if isinstance(content, str) else content
    except (json.JSONDecodeError, TypeError):
        parsed = None
    payload = {
        "_context_summary": True,
        "original_chars": len(raw),
        "summary": _compact_value(parsed if parsed is not None else raw),
    }
    return _truncate(_json(payload), token_limit)


def _bounded_tool_message(message: ToolMessage, token_limit: int) -> ToolMessage:
    content = summarize_tool_content(message.content, token_limit)
    if content == _text(message.content):
        return message
    try:
        return message.model_copy(update={"content": content})
    except AttributeError:
        return ToolMessage(
            content=content,
            tool_call_id=message.tool_call_id,
            name=message.name,
            id=message.id,
        )


def _message_tokens(message: BaseMessage) -> int:
    content = _text(getattr(message, "content", ""))
    tool_calls = _text(getattr(message, "tool_calls", None))
    return estimate_tokens(content) + estimate_tokens(tool_calls) + 4


def _recent_messages(messages: Iterable[BaseMessage], budget: ContextBudget) -> list[BaseMessage]:
    """Pack the newest protocol-valid conversational suffix into its allocation."""
    candidates: list[BaseMessage] = []
    for message in messages:
        # Application-created system/document messages are rebuilt as bounded sections below.
        if isinstance(message, SystemMessage):
            continue
        candidates.append(
            _bounded_tool_message(message, budget.tool_result)
            if isinstance(message, ToolMessage)
            else message
        )
    candidates = candidates[-budget.max_recent_messages:]

    selected: list[BaseMessage] = []
    used = 0
    for message in reversed(candidates):
        cost = _message_tokens(message)
        if selected and used + cost > budget.recent_messages:
            break
        if not selected and cost > budget.recent_messages:
            if isinstance(message, (HumanMessage, ToolMessage)):
                try:
                    message = message.model_copy(
                        update={"content": _truncate(_text(message.content), budget.recent_messages - 8)}
                    )
                    cost = _message_tokens(message)
                except AttributeError:
                    pass
        selected.append(message)
        used += cost
    selected.reverse()

    # Never send an orphan ToolMessage at the start of the window.
    while selected and isinstance(selected[0], ToolMessage):
        selected.pop(0)
    return selected


def _section(title: str, value: Any, token_limit: int) -> str:
    body = _truncate(_text(value), token_limit)
    return f"\n\n## {title}\n{body}" if body else ""


def _evidence_payload(
    state: Mapping[str, Any],
    laws: list[dict[str, Any]],
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if laws:
        payload["retrieved_laws"] = laws
    if cases:
        payload["retrieved_cases"] = cases
    reports = list(state.get("agent_reports") or [])
    if reports:
        payload["specialist_reports"] = reports[-6:]
    if state.get("uploaded_doc_text"):
        payload["uploaded_document"] = {
            "name": state.get("uploaded_doc_name") or "未命名文档",
            "content": state.get("uploaded_doc_text"),
        }
    if state.get("uploaded_evidence_text"):
        payload["uploaded_evidence"] = state.get("uploaded_evidence_text")
    return payload


def build_model_context(
    state: Mapping[str, Any],
    base_system_prompt: str,
    *,
    task_context: Mapping[str, Any] | str | None = None,
    budget: ContextBudget | None = None,
) -> BuiltContext:
    """Construct system + memory + summary + recent turns + plan + Top-N evidence."""
    budget = budget or ContextBudget()
    laws = select_top_evidence(state.get("retrieved_laws") or [], budget.law_top_n)
    cases = select_top_evidence(state.get("retrieved_cases") or [], budget.case_top_n)

    system_base = _truncate(base_system_prompt, min(budget.system, budget.prompt_tokens))
    relevant_memory = {
        key: state.get(key)
        for key in ("memory_profile", "memory_longterm", "viking_context")
        if state.get(key)
    }
    sections = {
        "system": system_base,
        "relevant_memory": _section("Relevant Memory", relevant_memory, budget.relevant_memory),
        "conversation_summary": _section(
            "Conversation Summary", state.get("memory_summary"), budget.summary
        ),
        "current_plan": _section("Current Plan", state.get("plan") or [], budget.current_plan),
        "retrieved_evidence": _section(
            "Retrieved Evidence (Top-N)",
            _evidence_payload(state, laws, cases),
            budget.evidence,
        ),
        "current_task": _section("Current Task", task_context, budget.current_task),
    }

    recent = _recent_messages(state.get("messages") or [], budget)
    recent_tokens = sum(_message_tokens(item) for item in recent)
    available_recent = max(0, budget.prompt_tokens - estimate_tokens(sections["system"]))
    while recent and recent_tokens > available_recent:
        if len(recent) > 1:
            recent.pop(0)
            while recent and isinstance(recent[0], ToolMessage):
                recent.pop(0)
        else:
            message = recent[0]
            content_budget = max(0, available_recent - 4 - estimate_tokens(_text(getattr(message, "tool_calls", None))))
            if content_budget <= 0:
                recent.clear()
            else:
                try:
                    recent[0] = message.model_copy(
                        update={"content": _truncate(_text(message.content), content_budget)}
                    )
                except AttributeError:
                    recent.clear()
        recent_tokens = sum(_message_tokens(item) for item in recent)

    # Reserve the bounded recent conversation before adding optional sections.
    # This guarantees that a large memory/document section cannot evict the
    # user's latest turn from the model invocation.
    assembled = sections["system"]
    optional_names = (
        "relevant_memory", "conversation_summary", "current_plan",
        "retrieved_evidence", "current_task",
    )
    for name in optional_names:
        remaining = budget.prompt_tokens - estimate_tokens(assembled) - recent_tokens
        if remaining <= 0:
            sections[name] = ""
            continue
        sections[name] = _truncate(sections[name], remaining)
        assembled += sections[name]

    section_tokens = {name: estimate_tokens(value) for name, value in sections.items()}
    total_tokens = sum(section_tokens.values()) + recent_tokens
    status = {
        "input_token_budget": budget.input_tokens,
        "output_token_reserve": budget.output_reserve,
        "prompt_token_budget": budget.prompt_tokens,
        "estimated_prompt_tokens": total_tokens,
        "usage_ratio": round(total_tokens / budget.prompt_tokens, 4),
        "section_tokens": {**section_tokens, "recent_messages": recent_tokens},
        "recent_message_count": len(recent),
        "source_message_count": len(list(state.get("messages") or [])),
        "selected_law_count": len(laws),
        "selected_case_count": len(cases),
        "tool_results_summarized": sum(
            1
            for message in recent
            if isinstance(message, ToolMessage) and '"_context_summary":true' in _text(message.content)
        ),
    }
    return BuiltContext(
        messages=[SystemMessage(content=assembled), *recent],
        system_prompt=assembled,
        selected_laws=laws,
        selected_cases=cases,
        status=status,
    )


__all__ = [
    "BuiltContext",
    "ContextBudget",
    "build_model_context",
    "select_top_evidence",
    "summarize_tool_content",
]
