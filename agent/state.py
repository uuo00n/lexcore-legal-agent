"""Agent 状态定义，以及并行节点写入列表字段时使用的合并规则。"""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class PlanStep(TypedDict, total=False):
    """Plan-and-Execute 中可独立调度、追踪和回写结果的计划步骤。"""

    step_id: str
    task_type: str
    description: str
    status: Literal["pending", "running", "completed", "failed", "skipped"]
    assigned_agent: str
    result: Any


class RetrievedLaw(TypedDict, total=False):
    """法条检索结果；兼容当前检索工具返回的扩展字段。"""

    law_name: str
    article_no: str
    content: str
    source_type: str
    source_id: str
    title: str


class RetrievedCase(TypedDict, total=False):
    """案例检索结果；具体数据源可以继续附加字段。"""

    case_id: str
    case_name: str
    court: str
    case_no: str
    judgment_date: str
    summary: str
    source_type: str
    source_id: str


class AgentReport(TypedDict, total=False):
    """专业 Agent 提交给 Supervisor 的结构化报告。"""

    report_id: str
    agent_name: str
    task_id: str
    findings: Any
    sources: list[dict[str, Any]]
    confidence: str

    # 兼容旧 checkpoint 与调用方；新报告统一使用上面的字段。
    agent: str
    step_id: str
    status: str
    summary: str
    draft_response: str
    result: Any


class StatuteReport(AgentReport, total=False):
    """法规检索 Agent 的结构化产物。"""

    query: str
    keywords: list[str]
    statutes: list[RetrievedLaw]
    relevance_assessment: list[dict[str, Any]]
    evidence_insufficient: bool


class Citation(TypedDict, total=False):
    """答案引用，可关联法条、案例或其他证据。"""

    citation_id: str
    source_type: str
    source_id: str
    title: str
    article_no: str
    content: str
    url: str


class VerificationResult(TypedDict, total=False):
    """Verifier 对答案、事实与引用完整性的结构化检查结果。"""

    passed: bool
    score: float
    issues: list[str]
    reason: str


def _stable_item_key(item: object) -> str:
    """为无统一业务主键的兼容字典生成稳定去重键。"""
    if isinstance(item, dict):
        for field in ("report_id", "citation_id", "case_id"):
            value = item.get(field)
            if value:
                return f"{field}:{value}"
        try:
            return json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            pass
    return repr(item)


def merge_unique_items(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    """去重追加列表；显式写入空列表时清空，兼容每轮请求的重置逻辑。"""
    if right == []:
        return []
    merged = list(left or [])
    seen = {_stable_item_key(item) for item in merged}
    for item in right or []:
        key = _stable_item_key(item)
        if key not in seen:
            merged.append(item)
            seen.add(key)
    return merged


def merge_plan_steps(
    left: list[PlanStep] | None,
    right: list[PlanStep] | None,
) -> list[PlanStep]:
    """按 ``step_id`` 合并计划，避免并行 Agent 的步骤更新整表覆盖。"""
    if right == []:
        return []
    merged: list[PlanStep] = [dict(step) for step in left or []]  # type: ignore[misc]
    positions = {
        step.get("step_id"): index
        for index, step in enumerate(merged)
        if step.get("step_id")
    }
    for step in right or []:
        step_copy: PlanStep = dict(step)  # type: ignore[assignment]
        step_id = step_copy.get("step_id")
        if step_id and step_id in positions:
            merged[positions[step_id]].update(step_copy)
        else:
            if step_id:
                positions[step_id] = len(merged)
            merged.append(step_copy)
    return merged


def merge_agent_reports(
    left: list[AgentReport] | None,
    right: list[AgentReport] | None,
) -> list[AgentReport]:
    """追加并去重 Agent 报告；保留项目原有的空列表清空语义。"""
    return merge_unique_items(left, right)  # type: ignore[return-value]


class AgentState(TypedDict, total=False):
    """LangGraph 全局状态；所有字段均为可选，以兼容旧 checkpoint 和调用方。"""

    # 会话身份与链路追踪
    messages: Annotated[list[BaseMessage], add_messages]  # 对话消息，按消息 ID 合并
    user_id: str  # 当前用户标识
    thread_id: str  # 会话线程标识
    trace_id: str  # 单次请求的链路追踪标识

    # 用户原始问题与查询改写结果
    original_query: str  # 未加工的用户问题
    rewritten_query: str  # 为检索或规划优化后的问题

    # 意图识别
    intent: str  # 识别出的业务意图
    intent_confidence: float  # 意图置信度，建议范围为 0 到 1

    # Supervisor 的路由决策
    supervisor_route: str  # 下一节点或 Agent 名称
    supervisor_reason: str  # 路由原因

    # Plan-and-Execute 规划状态
    plan: Annotated[list[PlanStep], merge_plan_steps]  # 全量结构化计划
    current_step: Optional[str]  # 当前步骤的 step_id
    completed_steps: Annotated[list[PlanStep], merge_plan_steps]  # 已完成步骤
    remaining_steps: Annotated[list[PlanStep], merge_plan_steps]  # 尚待执行步骤

    # 检索证据；reducer 防止并行检索结果相互覆盖
    retrieved_laws: Annotated[list[RetrievedLaw], merge_unique_items]
    retrieved_cases: Annotated[list[RetrievedCase], merge_unique_items]

    # 专业 Agent 报告；空列表仍可在新请求开始时清空历史报告
    agent_reports: Annotated[list[AgentReport], merge_agent_reports]

    # Verifier 结果与最终引用
    verification_result: Optional[VerificationResult]
    citations: Annotated[list[Citation], merge_unique_items]

    # 重试和工具调用保护计数；沿用绝对值写入，避免改变现有节点语义
    retry_count: int
    tool_call_count: int

    # 分层记忆上下文（由 memory_node 填充）
    memory_profile: Optional[str]  # 用户画像
    memory_longterm: Optional[str]  # 检索到的相关长期记忆
    memory_summary: Optional[str]  # 历史摘要

    # 用户上传文档
    uploaded_doc_text: Optional[str]  # 上传文档解析后的正文
    uploaded_doc_name: Optional[str]  # 上传文档文件名

    # 以下字段为现有执行链路使用的兼容字段，暂不删除
    uploaded_evidence_id: Optional[str]  # 上传的视频证据标识
    uploaded_evidence_text: Optional[str]  # 视频证据的摘要文本
    evidence_insufficient: bool  # 当前检索证据是否不足
    needs_follow_up: bool  # 是否需要用户补充事实
    supervisor_finalized: bool  # Supervisor 是否已生成最终答复
    viking_context: Optional[str]  # OpenViking Resource/Memory/Skill 上下文
    viking_context_hits: Annotated[list[dict[str, Any]], merge_unique_items]  # 上下文命中项
    context_status: dict[str, Any]  # 上下文压缩状态详情
    context_compacted: bool  # 本轮是否执行过上下文压缩
