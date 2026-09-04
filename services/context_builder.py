"""Build bounded model context from the agent's layered memory and working state.

Budgets are tiered rather than flat: ``standard`` covers ordinary legal Q&A,
``complex`` the default per-task budget for case analysis, and ``long`` long
contracts, several uploaded exhibits, or large batches of retrieved cases. Every
tier is derived from ``CONTEXT_INPUT_TOKEN_BUDGET`` and clamped to
``CONTEXT_MODEL_MAX_TOKENS``, so one knob moves the whole table coherently.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage

from services.memory import estimate_tokens


# Declared ceiling of the smallest context window among the routed models. Every
# tier is clamped to it, so a smaller model never receives an oversized prompt.
DEFAULT_MODEL_MAX_TOKENS = 128_000
# Default per-task input budget. Tiers are derived from this value, so lowering it
# scales the whole table down instead of leaving the tiers inconsistent.
DEFAULT_INPUT_TOKENS = 64_000
DEFAULT_OUTPUT_RESERVE = 8_000
DEFAULT_RECENT_MESSAGE_COUNT = 12
DEFAULT_LAW_TOP_N = 6
DEFAULT_CASE_TOP_N = 4
# Long-context escalation thresholds: long contract, several uploaded exhibits, or
# a large batch of retrieved cases. The case threshold stays above the complex
# tier's own Top-N so a merely saturated case list does not escalate by itself.
DEFAULT_LONG_MATERIAL_TOKENS = 4_000
DEFAULT_LONG_CASE_COUNT = 8

# Share of the prompt budget each layer may occupy. These are soft caps that scale
# with the tier; the prompt budget itself stays the hard limit enforced below.
_LAYER_SHARES: dict[str, float] = {
    "system": 0.09,
    "relevant_memory": 0.08,
    "summary": 0.06,
    "recent_messages": 0.24,
    "current_plan": 0.06,
    "evidence": 0.37,
    "current_task": 0.10,
    # Per tool observation rather than a prompt layer, so it is excluded from the sum.
    "tool_result": 0.02,
}
_LAYER_ENV: dict[str, str] = {
    "system": "CONTEXT_SYSTEM_TOKEN_BUDGET",
    "relevant_memory": "CONTEXT_MEMORY_TOKEN_BUDGET",
    "summary": "CONTEXT_SUMMARY_TOKEN_BUDGET",
    "recent_messages": "CONTEXT_RECENT_MESSAGES_TOKEN_BUDGET",
    "current_plan": "CONTEXT_PLAN_TOKEN_BUDGET",
    "evidence": "CONTEXT_EVIDENCE_TOKEN_BUDGET",
    "current_task": "CONTEXT_CURRENT_TASK_TOKEN_BUDGET",
    "tool_result": "CONTEXT_TOOL_RESULT_TOKEN_BUDGET",
}
# Content-bearing fields used to size the material a request has to reason over.
_MATERIAL_FIELDS = (
    "content", "summary", "court_reasoning", "judgment_result", "dispute_focus", "findings",
)


@dataclass(frozen=True)
class TierScale:
    """One context tier, expressed relative to the base per-task budget."""

    input_ratio: float
    output_reserve_ratio: float
    # Scales evidence Top-N and recent-message counts: a larger window is useless
    # while item counts stay pinned to the smallest tier.
    capacity_ratio: float


# standard 普通法律问答 / complex 复杂案件分析 / long 长合同、多份证据、大量类案。
TIERS: dict[str, TierScale] = {
    "standard": TierScale(input_ratio=0.5, output_reserve_ratio=1.0, capacity_ratio=1.0),
    "complex": TierScale(input_ratio=1.0, output_reserve_ratio=1.5, capacity_ratio=1.6),
    "long": TierScale(input_ratio=2.0, output_reserve_ratio=2.0, capacity_ratio=2.5),
}
# Used when the complexity router has not run yet: the plain per-task default.
DEFAULT_TIER = "complex"
# A tier expects to spend between half and all of its input budget.
TARGET_USAGE_FLOOR_RATIO = 0.5


def _positive_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _optional_env_int(name: str) -> int | None:
    """Read an operator override, ignoring unset and unparsable values."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return None


def _tier_env(tier: str, suffix: str) -> int | None:
    return _optional_env_int(f"CONTEXT_TIER_{tier.upper()}_{suffix}")


def model_max_tokens() -> int:
    """Declared ceiling for a single request's input; set it to the smallest routed window."""
    return _positive_env("CONTEXT_MODEL_MAX_TOKENS", DEFAULT_MODEL_MAX_TOKENS)


def base_input_tokens() -> int:
    return min(_positive_env("CONTEXT_INPUT_TOKEN_BUDGET", DEFAULT_INPUT_TOKENS), model_max_tokens())


def base_output_reserve() -> int:
    return _positive_env("CONTEXT_OUTPUT_TOKEN_RESERVE", DEFAULT_OUTPUT_RESERVE)


def _tier_scale(tier: str) -> TierScale:
    return TIERS.get(tier, TIERS[DEFAULT_TIER])


def tier_input_tokens(tier: str) -> int:
    explicit = _tier_env(tier, "INPUT_TOKENS")
    value = explicit or round(base_input_tokens() * _tier_scale(tier).input_ratio)
    return max(1, min(value, model_max_tokens()))


def tier_output_reserve(tier: str) -> int:
    explicit = _tier_env(tier, "OUTPUT_RESERVE")
    value = explicit or round(base_output_reserve() * _tier_scale(tier).output_reserve_ratio)
    # A reserve that eats the whole window would leave nothing to prompt with.
    return max(1, min(value, max(1, tier_input_tokens(tier) // 2)))


def tier_prompt_tokens(tier: str) -> int:
    return max(1, tier_input_tokens(tier) - tier_output_reserve(tier))


def _layer_budget(name: str, prompt_tokens: int) -> int:
    explicit = _optional_env_int(_LAYER_ENV[name])
    return explicit or max(1, round(prompt_tokens * _LAYER_SHARES[name]))


def _tier_count(tier: str, suffix: str, env_name: str, default: int) -> int:
    """Scale a base count by the tier's capacity, unless the tier overrides it."""
    explicit = _tier_env(tier, suffix)
    if explicit:
        return explicit
    return max(1, round(_positive_env(env_name, default) * _tier_scale(tier).capacity_ratio))


def tier_law_top_n(tier: str) -> int:
    return _tier_count(tier, "LAW_TOP_N", "CONTEXT_RETRIEVED_LAW_TOP_N", DEFAULT_LAW_TOP_N)


def tier_case_top_n(tier: str) -> int:
    return _tier_count(tier, "CASE_TOP_N", "CONTEXT_RETRIEVED_CASE_TOP_N", DEFAULT_CASE_TOP_N)


def tier_recent_message_count(tier: str) -> int:
    return _tier_count(
        tier, "RECENT_MESSAGE_COUNT", "CONTEXT_RECENT_MESSAGE_COUNT", DEFAULT_RECENT_MESSAGE_COUNT
    )


def retained_law_top_n() -> int:
    """Working-state retention cap: the highest Top-N any tier can ask for."""
    return max(tier_law_top_n(tier) for tier in TIERS)


def retained_case_top_n() -> int:
    return max(tier_case_top_n(tier) for tier in TIERS)


def max_tier_recent_messages() -> int:
    return max(tier_recent_message_count(tier) for tier in TIERS)


@dataclass(frozen=True)
class ContextBudget:
    """Input allocation. Output reserve is never made available to prompt content."""

    input_tokens: int = field(default_factory=lambda: tier_input_tokens(DEFAULT_TIER))
    output_reserve: int = field(default_factory=lambda: tier_output_reserve(DEFAULT_TIER))
    system: int = field(default_factory=lambda: _layer_budget("system", tier_prompt_tokens(DEFAULT_TIER)))
    relevant_memory: int = field(
        default_factory=lambda: _layer_budget("relevant_memory", tier_prompt_tokens(DEFAULT_TIER))
    )
    summary: int = field(default_factory=lambda: _layer_budget("summary", tier_prompt_tokens(DEFAULT_TIER)))
    recent_messages: int = field(
        default_factory=lambda: _layer_budget("recent_messages", tier_prompt_tokens(DEFAULT_TIER))
    )
    current_plan: int = field(
        default_factory=lambda: _layer_budget("current_plan", tier_prompt_tokens(DEFAULT_TIER))
    )
    evidence: int = field(default_factory=lambda: _layer_budget("evidence", tier_prompt_tokens(DEFAULT_TIER)))
    current_task: int = field(
        default_factory=lambda: _layer_budget("current_task", tier_prompt_tokens(DEFAULT_TIER))
    )
    tool_result: int = field(
        default_factory=lambda: _layer_budget("tool_result", tier_prompt_tokens(DEFAULT_TIER))
    )
    max_recent_messages: int = field(default_factory=lambda: tier_recent_message_count(DEFAULT_TIER))
    law_top_n: int = field(default_factory=lambda: tier_law_top_n(DEFAULT_TIER))
    case_top_n: int = field(default_factory=lambda: tier_case_top_n(DEFAULT_TIER))
    tier: str = DEFAULT_TIER

    @property
    def prompt_tokens(self) -> int:
        return max(1, self.input_tokens - self.output_reserve)

    @property
    def target_input_tokens(self) -> tuple[int, int]:
        """Expected input usage band for this tier; purely an observability signal."""
        return round(self.input_tokens * TARGET_USAGE_FLOOR_RATIO), self.input_tokens


def budget_for_tier(tier: str) -> ContextBudget:
    """Build the full allocation for one tier, honouring per-tier env overrides."""
    name = tier if tier in TIERS else DEFAULT_TIER
    prompt_tokens = tier_prompt_tokens(name)
    return ContextBudget(
        input_tokens=tier_input_tokens(name),
        output_reserve=tier_output_reserve(name),
        system=_layer_budget("system", prompt_tokens),
        relevant_memory=_layer_budget("relevant_memory", prompt_tokens),
        summary=_layer_budget("summary", prompt_tokens),
        recent_messages=_layer_budget("recent_messages", prompt_tokens),
        current_plan=_layer_budget("current_plan", prompt_tokens),
        evidence=_layer_budget("evidence", prompt_tokens),
        current_task=_layer_budget("current_task", prompt_tokens),
        tool_result=_layer_budget("tool_result", prompt_tokens),
        max_recent_messages=tier_recent_message_count(name),
        law_top_n=tier_law_top_n(name),
        case_top_n=tier_case_top_n(name),
        tier=name,
    )


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


@dataclass(frozen=True)
class ContextTierDecision:
    """Why a request landed in one tier; recorded in the ``context_build`` trace."""

    tier: str
    reason: str
    signals: list[str]
    material_tokens: int


def _material_tokens(state: Mapping[str, Any]) -> int:
    """Size the material this request must reason over, ignoring prompt scaffolding."""
    total = estimate_tokens(_text(state.get("uploaded_doc_text")))
    total += estimate_tokens(_text(state.get("uploaded_evidence_text")))
    for group in ("retrieved_laws", "retrieved_cases", "agent_reports"):
        for item in state.get(group) or []:
            if isinstance(item, Mapping):
                total += sum(estimate_tokens(_text(item.get(field))) for field in _MATERIAL_FIELDS)
            else:
                total += estimate_tokens(_text(item))
    return total


def resolve_context_tier(state: Mapping[str, Any]) -> ContextTierDecision:
    """Pick a tier deterministically from material size and the complexity router."""
    material_tokens = _material_tokens(state)
    case_count = len(list(state.get("retrieved_cases") or []))
    signals: list[str] = []
    if material_tokens >= _positive_env("CONTEXT_LONG_MATERIAL_TOKENS", DEFAULT_LONG_MATERIAL_TOKENS):
        signals.append("long_material")
    if case_count >= _positive_env("CONTEXT_LONG_CASE_COUNT", DEFAULT_LONG_CASE_COUNT):
        signals.append("many_cases")
    if signals:
        return ContextTierDecision("long", signals[0], signals, material_tokens)

    level = str(state.get("complexity_level") or "").strip().lower()
    legacy = str(state.get("task_complexity") or "").strip().lower()
    if level == "complex" or legacy == "high":
        return ContextTierDecision("complex", f"complexity_{level or legacy}", [level or legacy], material_tokens)
    if level in {"simple", "medium"} or legacy in {"low", "medium"}:
        return ContextTierDecision("standard", f"complexity_{level or legacy}", [level or legacy], material_tokens)
    # The complexity router has not run: fall back to the per-task default budget.
    return ContextTierDecision(DEFAULT_TIER, "unrouted", [], material_tokens)


def resolve_context_budget(state: Mapping[str, Any]) -> tuple[ContextBudget, ContextTierDecision]:
    decision = resolve_context_tier(state)
    return budget_for_tier(decision.tier), decision


def build_model_context(
    state: Mapping[str, Any],
    base_system_prompt: str,
    *,
    task_context: Mapping[str, Any] | str | None = None,
    budget: ContextBudget | None = None,
) -> BuiltContext:
    """Construct system + memory + summary + recent turns + plan + Top-N evidence."""
    if budget is None:
        budget, decision = resolve_context_budget(state)
    else:
        # An explicit budget wins, but the trace still needs comparable tier fields.
        decision = ContextTierDecision(budget.tier, "explicit_budget", [], _material_tokens(state))
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
    target_floor, target_ceiling = budget.target_input_tokens
    status = {
        "context_tier": budget.tier,
        "tier_reason": decision.reason,
        "tier_signals": decision.signals,
        "model_max_tokens": model_max_tokens(),
        "material_tokens": decision.material_tokens,
        "input_token_budget": budget.input_tokens,
        "output_token_reserve": budget.output_reserve,
        "prompt_token_budget": budget.prompt_tokens,
        "estimated_prompt_tokens": total_tokens,
        # What the request is expected to occupy of the window, reserve included.
        "estimated_input_tokens": total_tokens + budget.output_reserve,
        "target_input_tokens": [target_floor, target_ceiling],
        "usage_ratio": round(total_tokens / budget.prompt_tokens, 4),
        "section_tokens": {**section_tokens, "recent_messages": recent_tokens},
        "recent_message_count": len(recent),
        "max_recent_messages": budget.max_recent_messages,
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
    "ContextTierDecision",
    "DEFAULT_TIER",
    "TIERS",
    "TierScale",
    "base_input_tokens",
    "base_output_reserve",
    "budget_for_tier",
    "build_model_context",
    "max_tier_recent_messages",
    "model_max_tokens",
    "resolve_context_budget",
    "resolve_context_tier",
    "retained_case_top_n",
    "retained_law_top_n",
    "select_top_evidence",
    "summarize_tool_content",
    "tier_case_top_n",
    "tier_input_tokens",
    "tier_law_top_n",
    "tier_output_reserve",
    "tier_prompt_tokens",
    "tier_recent_message_count",
]
