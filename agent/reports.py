"""Specialist report normalization helpers."""
from __future__ import annotations

from typing import Any

from agent.state import AgentState


def report_task_id(state: AgentState, agent_name: str) -> str:
    """Return a stable task identifier for one specialist invocation."""
    return str(
        state.get("current_step")
        or state.get("trace_id")
        or f"current-request:{agent_name}"
    )


def normalize_sources(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Keep source metadata compact and deduplicate it."""
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in items or []:
        source = dict(item)
        source_type = str(source.get("source_type") or "")
        source_id = str(source.get("source_id") or source.get("case_id") or "")
        title = str(source.get("title") or source.get("law_name") or source.get("case_name") or "")
        article_no = str(source.get("article_no") or "")
        key = (source_type, source_id, title, article_no)
        if key in seen:
            continue
        seen.add(key)
        result.append(source)
    return result


def build_agent_report(
    state: AgentState,
    agent_name: str,
    *,
    summary: str,
    findings: Any,
    sources: list[dict[str, Any]] | None = None,
    confidence: str = "medium",
    **extra: Any,
) -> dict[str, Any]:
    """Build the common report envelope while retaining legacy aliases."""
    task_id = report_task_id(state, agent_name)
    report = {
        "report_id": f"{task_id}:{agent_name}",
        "agent_name": agent_name,
        "task_id": task_id,
        "summary": summary,
        "findings": findings,
        "sources": normalize_sources(sources),
        "confidence": confidence if confidence in {"low", "medium", "high"} else "medium",
        "agent": agent_name,
        **extra,
    }
    return report


def report_agent_name(report: dict[str, Any]) -> str:
    """Read both the new field and old checkpoint alias."""
    return str(report.get("agent_name") or report.get("agent") or "")
