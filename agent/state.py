"""Agent 状态定义 —— LangGraph 图中流转的状态结构。"""
from __future__ import annotations

from typing import Annotated, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


def merge_agent_reports(left: list[dict] | None, right: list[dict] | None) -> list[dict]:
    if right == []:
        return []
    return (left or []) + (right or [])


class AgentState(TypedDict, total=False):
    """智能体状态，在图的各节点间传递。"""
    messages: Annotated[list[BaseMessage], add_messages]
    uploaded_doc_text: Optional[str]
    uploaded_doc_name: Optional[str]
    uploaded_evidence_id: Optional[str]
    uploaded_evidence_text: Optional[str]
    retrieved_laws: list[dict]
    thread_id: str
    trace_id: str
    needs_follow_up: bool
    supervisor_route: str
    supervisor_reason: str
    agent_reports: Annotated[list[dict], merge_agent_reports]
    supervisor_finalized: bool
    # 分层记忆上下文（由 memory_node 填充）
    memory_profile: Optional[str]    # 用户画像
    memory_longterm: Optional[str]   # 检索到的相关长期记忆
    memory_summary: Optional[str]    # 历史摘要
    viking_context: Optional[str]    # OpenViking 风格 Resource/Memory/Skill 上下文
    viking_context_hits: list[dict]  # 命中的 viking:// 上下文路径
    context_status: dict
    context_compacted: bool
    # ReAct 循环计数
    tool_call_count: int
