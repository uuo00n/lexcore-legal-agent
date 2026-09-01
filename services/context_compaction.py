"""Runtime context compaction for LangGraph checkpoints."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.modifier import RemoveMessage

from services.llm import get_llm
from services.memory import (
    CHARS_PER_TOKEN,
    SLIDING_WINDOW_SIZE,
    estimate_tokens,
    get_summary,
    get_summary_msg_count,
    get_user_profile,
    save_summary,
    save_user_profile,
)

log = logging.getLogger(__name__)

DOC_PREFIX = "[USER_DOCUMENT]"
DEFAULT_COMPACTION_MODEL = "deepseek-v4-flash"


@dataclass(frozen=True)
class ContextCompactionConfig:
    keep_recent: int = SLIDING_WINDOW_SIZE
    token_budget: int = int(os.getenv("CONTEXT_WINDOW_TOKEN_BUDGET", "12000"))
    auto_compact_ratio: float = float(os.getenv("CONTEXT_AUTO_COMPACT_RATIO", "0.75"))
    auto_compact_messages: int = int(os.getenv("CONTEXT_AUTO_COMPACT_MESSAGES", "16"))


_COMPACTION_PROMPT = """你是法律咨询智能体的上下文压缩器。请把旧对话压缩成可恢复上下文的 JSON。

要求：
- 只保留对后续法律咨询有用的信息。
- 不编造事实、法条或结论。
- 区分稳定用户画像和当前案件事实。
- JSON 必须可解析，不要输出 Markdown 代码块。

已有摘要：
{existing_summary}

需要压缩的旧消息：
{messages}

返回 JSON，格式：
{{
  "summary": "300字以内的滚动摘要",
  "entities": {{
    "identity": "稳定身份，如员工/企业主/学生；未知为空",
    "focus_areas": ["稳定关注领域"],
    "preferences": ["稳定交互偏好"]
  }},
  "case_profile": {{
    "parties": ["当前案件当事人"],
    "facts": ["当前案件关键事实"],
    "dates": ["关键日期"],
    "amounts": ["金额"],
    "documents": ["证据或文件"]
  }},
  "open_questions": ["仍需追问的问题"],
  "legal_focus": ["当前法律焦点"]
}}"""


def _get_compaction_llm():
    model = os.getenv("CONTEXT_COMPACTION_MODEL") or os.getenv(
        "MEMORY_EXTRACTOR_MODEL",
        DEFAULT_COMPACTION_MODEL,
    )
    return get_llm(model=model, model_route="context_compaction", streaming=False)


def _message_content(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


def _message_role(message: BaseMessage) -> str:
    if isinstance(message, HumanMessage):
        return "用户"
    if isinstance(message, AIMessage):
        return "助手"
    if isinstance(message, ToolMessage):
        return "工具"
    if isinstance(message, SystemMessage):
        return "系统"
    return message.__class__.__name__


def _is_user_document_message(message: BaseMessage) -> bool:
    return isinstance(message, SystemMessage) and _message_content(message).startswith(DOC_PREFIX)


def _dedupe(items: list[Any]) -> list[Any]:
    result = []
    seen = set()
    for item in items:
        if item is None:
            continue
        if isinstance(item, str):
            item = item.strip()
            if not item:
                continue
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def _compactable_prefix(messages: list[BaseMessage], keep_recent: int) -> list[BaseMessage]:
    if len(messages) <= keep_recent:
        return []
    prefix = messages[:-keep_recent]
    return [message for message in prefix if not _is_user_document_message(message)]


def _estimate_message_tokens(message: BaseMessage) -> int:
    return estimate_tokens(_message_content(message)) + 4


def build_context_status(
    messages: list[BaseMessage] | None,
    *,
    extra_texts: list[str] | None = None,
    config: ContextCompactionConfig | None = None,
) -> dict[str, Any]:
    config = config or ContextCompactionConfig()
    messages = list(messages or [])
    extra_texts = extra_texts or []
    estimated_tokens = sum(_estimate_message_tokens(message) for message in messages)
    estimated_tokens += sum(estimate_tokens(text) for text in extra_texts if text)
    compactable_messages = len(_compactable_prefix(messages, config.keep_recent))
    usage_ratio = estimated_tokens / config.token_budget if config.token_budget else 0
    should_compact = compactable_messages > 0 and (
        len(messages) > config.auto_compact_messages
        or usage_ratio >= config.auto_compact_ratio
    )
    return {
        "message_count": len(messages),
        "compactable_messages": compactable_messages,
        "estimated_tokens": estimated_tokens,
        "token_budget": config.token_budget,
        "usage_ratio": round(usage_ratio, 4),
        "auto_compact_ratio": config.auto_compact_ratio,
        "auto_compact_messages": config.auto_compact_messages,
        "keep_recent": config.keep_recent,
        "should_compact": should_compact,
    }


def merge_profile_entities(
    existing_profile: dict[str, Any] | None,
    entities: dict[str, Any] | None,
    case_profile: dict[str, Any] | None,
    *,
    open_questions: list[Any] | None = None,
    legal_focus: list[Any] | None = None,
) -> dict[str, Any]:
    merged = dict(existing_profile or {})
    entities = entities or {}
    case_profile = case_profile or {}

    identity = str(entities.get("identity") or entities.get("user_identity") or "").strip()
    if identity and not str(merged.get("identity") or "").strip():
        merged["identity"] = identity

    for key in ("focus_areas", "preferences"):
        merged[key] = _dedupe(_as_list(merged.get(key)) + _as_list(entities.get(key)))

    current_case = dict(merged.get("case_profile") or {})
    for key in ("parties", "facts", "dates", "amounts", "documents"):
        current_case[key] = _dedupe(_as_list(current_case.get(key)) + _as_list(case_profile.get(key)))
    current_case["open_questions"] = _dedupe(
        _as_list(current_case.get("open_questions")) + _as_list(open_questions)
    )
    current_case["legal_focus"] = _dedupe(
        _as_list(current_case.get("legal_focus")) + _as_list(legal_focus)
    )
    if any(current_case.get(key) for key in current_case):
        merged["case_profile"] = current_case

    return merged


def _format_messages_for_prompt(messages: list[BaseMessage]) -> str:
    lines = []
    for index, message in enumerate(messages, start=1):
        content = _message_content(message).strip()
        if not content:
            continue
        lines.append(f"{index}. {_message_role(message)}：{content[:1200]}")
    return "\n".join(lines)


def _parse_compaction_payload(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"summary": text.strip()}
    return parsed if isinstance(parsed, dict) else {"summary": text.strip()}


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    return content if isinstance(content, str) else str(content)


def _summary_msg_count(thread_id: str, compacted_count: int) -> int:
    try:
        existing = get_summary_msg_count(thread_id)
    except Exception:
        existing = 0
    # ``msg_count`` is an archive offset, not a count of checkpoint deletions.
    # Runtime compaction can summarize messages already covered by the
    # background archiver, so adding here would skip future archive entries.
    return max(existing, compacted_count)


async def compact_state_context(
    state: dict[str, Any],
    *,
    force: bool = False,
    config: ContextCompactionConfig | None = None,
) -> dict[str, Any]:
    config = config or ContextCompactionConfig()
    messages = list(state.get("messages") or [])
    extra_texts = [
        str(state.get("uploaded_doc_text") or ""),
        str(state.get("memory_summary") or ""),
        str(state.get("memory_longterm") or ""),
        str(state.get("viking_context") or ""),
    ]
    before_status = build_context_status(messages, extra_texts=extra_texts, config=config)
    compactable = _compactable_prefix(messages, config.keep_recent)
    if not force and not before_status["should_compact"]:
        return {"context_status": before_status, "context_compacted": False}
    if not compactable:
        return {"context_status": before_status, "context_compacted": False}

    thread_id = str(state.get("thread_id") or "").strip()
    try:
        existing_summary = get_summary(thread_id) or "（暂无历史摘要）" if thread_id else "（暂无历史摘要）"
        prompt = _COMPACTION_PROMPT.format(
            existing_summary=existing_summary,
            messages=_format_messages_for_prompt(compactable),
        )
        response = await _get_compaction_llm().ainvoke(prompt)
        payload = _parse_compaction_payload(_response_text(response))
    except Exception as exc:
        log.warning("上下文压缩失败: %s", exc)
        return {"context_status": before_status, "context_compacted": False, "context_compaction_error": str(exc)}

    summary = str(payload.get("summary") or "").strip()
    entities = payload.get("entities") if isinstance(payload.get("entities"), dict) else {}
    case_profile = payload.get("case_profile") if isinstance(payload.get("case_profile"), dict) else {}
    open_questions = _as_list(payload.get("open_questions"))
    legal_focus = _as_list(payload.get("legal_focus"))

    if thread_id and summary:
        try:
            save_summary(thread_id, summary, _summary_msg_count(thread_id, len(compactable)))
        except Exception as exc:
            log.warning("压缩摘要保存失败: %s", exc)

    if thread_id and (entities or case_profile or open_questions or legal_focus):
        try:
            profile = merge_profile_entities(
                get_user_profile(thread_id) or {},
                entities,
                case_profile,
                open_questions=open_questions,
                legal_focus=legal_focus,
            )
            save_user_profile(thread_id, profile)
        except Exception as exc:
            log.warning("压缩实体记忆保存失败: %s", exc)

    removals = [
        RemoveMessage(id=message.id)
        for message in compactable
        if getattr(message, "id", None)
    ]
    removed_ids = {message.id for message in compactable if getattr(message, "id", None)}
    remaining_messages = [
        message
        for message in messages
        if not getattr(message, "id", None) or message.id not in removed_ids
    ]
    after_status = build_context_status(remaining_messages, extra_texts=extra_texts + [summary], config=config)
    after_status["last_compaction"] = {
        "removed_messages": len(removals),
        "summarized_messages": len(compactable),
        "summary_chars": len(summary),
    }
    return {
        "messages": removals,
        "memory_summary": summary or state.get("memory_summary", ""),
        "context_status": after_status,
        "context_compacted": bool(removals),
    }
