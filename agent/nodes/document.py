"""Uploaded document and evidence injection node."""
from __future__ import annotations

from typing import Any

from langchain_core.messages import SystemMessage

from agent.state import AgentState

DOC_PREFIX = "[USER_DOCUMENT]"
EVIDENCE_PREFIX = "[上传视频证据]"


def inject_doc_node(state: AgentState) -> dict[str, Any]:
    """Inject uploaded content once as system messages."""
    doc = state.get("uploaded_doc_text")
    evidence = state.get("uploaded_evidence_text")
    if not doc and not evidence:
        return {}

    messages = state.get("messages", [])
    additions: list[SystemMessage] = []
    has_doc_msg = any(
        isinstance(message, SystemMessage) and message.content.startswith(DOC_PREFIX)
        for message in messages
    )
    if doc and not has_doc_msg:
        name = state.get("uploaded_doc_name") or "未命名文档"
        additions.append(SystemMessage(content=f"{DOC_PREFIX} 文件名：{name}\n\n{doc}"))

    has_evidence_msg = any(
        isinstance(message, SystemMessage)
        and message.content.startswith(EVIDENCE_PREFIX)
        for message in messages
    )
    if evidence and not has_evidence_msg:
        additions.append(SystemMessage(content=f"{EVIDENCE_PREFIX}\n{evidence}"))

    return {"messages": additions} if additions else {}
