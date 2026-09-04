"""Agent 状态定义，以及并行节点写入列表字段时使用的合并规则。"""
from __future__ import annotations

import json
from collections.abc import Callable
from enum import Enum
from typing import Annotated, Any, Literal, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from agent.evidence import case_evidence_key, law_evidence_key


class TaskType(str, Enum):
    """Planner 可分派的任务类型。"""

    CASE_ANALYSIS = "case_analysis"
    STATUTE_RETRIEVAL = "statute_retrieval"
    CASE_RETRIEVAL = "case_retrieval"
    LEGAL_CONSULTATION = "legal_consultation"


class PlanStep(TypedDict, total=False):
    """Plan-and-Execute 中可独立调度、追踪和回写结果的计划步骤。"""

    step_id: str
    task_type: TaskType
    description: str
    status: Literal["pending", "running", "completed", "failed", "skipped"]
    assigned_agent: str
    required: bool
    result: Any


class RetrievedLaw(TypedDict, total=False):
    """规范化后的法条证据；字段与 ``agent.evidence.LawEvidence`` 保持一致。"""

    evidence_id: str
    ref_id: str
    canonical_law_id: str
    canonical_law_name: str
    display_law_name: str
    law_name: str
    article_no: str
    canonical_article_no: str
    content: str
    source_type: str
    source_id: str
    title: str
    level_name: str
    publish_date: str
    active_date: str
    timeliness_name: str
    validity: str
    relevance_score: float


class RetrievedCase(TypedDict, total=False):
    """规范化后的类案证据；字段与 ``agent.evidence.CaseEvidence`` 保持一致。"""

    evidence_id: str
    ref_id: str
    case_id: str
    case_name: str
    court: str
    case_no: str
    judgment_date: str
    summary: str
    dispute_focus: str
    court_reasoning: str
    judgment_result: str
    source_type: str
    source_id: str
    relevance_score: float


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


class CaseRetrievalReport(AgentReport, total=False):
    """类案检索 Agent 的结构化产物（§五）；只含本轮检索到的案例。"""

    query: str
    keywords: list[str]
    cases: list[RetrievedCase]
    relevance_assessment: list[dict[str, Any]]
    evidence_insufficient: bool


class CaseFacts(TypedDict, total=False):
    """Fact Analysis Agent 的结构化产物（§四）；只描述事实，不含任何法条引用。"""

    legal_relationship: str
    facts: list[str]
    legal_issues: list[str]
    missing_facts: list[str]
    facts_sufficient: bool
    needs_clarification: bool
    clarification_questions: list[str]

    # 事实充分性判定的可追溯依据
    category: str
    missing_dimensions: list[str]
    source: Literal["deterministic", "llm", "merged"]


class Citation(TypedDict, total=False):
    """答案引用，可关联法条、案例或其他证据；由 verified_evidence 派生。"""

    citation_id: str
    kind: Literal["law", "case"]
    evidence_id: str
    ref_id: str
    source_type: str
    source_id: str
    title: str
    law_name: str
    article_no: str
    case_no: str
    content: str
    url: str


class VerificationIssue(TypedDict, total=False):
    """结构化核验问题；Repair Router 依据 ``type`` 决定局部修复目标。"""

    type: str
    severity: Literal["blocking", "warning"]
    source: Literal["deterministic", "semantic"]
    step_id: str
    agent: str
    evidence_id: str
    message: str


class CitationCheck(TypedDict, total=False):
    """单条引用的确定性核验明细。"""

    kind: Literal["law", "case"]
    label: str
    report_id: str
    agent: str
    evidence_id: str
    ref_id: str
    canonical_law_id: str
    canonical_article_no: str
    verified: bool
    reason: str


class VerifiedEvidence(TypedDict, total=False):
    """P0-1 唯一引用真相源：核验通过的证据、引用与统计。"""

    laws: list[RetrievedLaw]
    cases: list[RetrievedCase]
    citations: list["Citation"]
    checks: list[CitationCheck]
    evidence_ids: list[str]
    citation_total: int
    citation_verified: int
    citation_unsupported: int


class VerificationResult(TypedDict, total=False):
    """Verifier 对答案、事实与引用完整性的结构化检查结果。"""

    passed: bool
    score: float
    issues: list[str]
    missing_sources: list[str]
    invalid_citations: list[str]
    needs_retry: bool
    retry_reason: Optional[str]

    # 拆分后的确定性 / 语义核验补充字段（§十四、P0-1、P1-6）
    structured_issues: list[VerificationIssue]
    citation_report: dict[str, int]
    verification_degraded: bool
    repair_targets: list[str]


class ToolLoopFailure(TypedDict, total=False):
    """Specialist ReAct loop 被保护机制终止时的结构化原因。"""

    agent_name: str
    task_id: str
    reason: str
    message: str
    tool_call_count: int
    max_tool_calls: int
    requested_tools: list[str]


def _stable_item_key(item: object) -> str:
    """为无统一业务主键的兼容字典生成稳定去重键。"""
    if isinstance(item, dict):
        for field in ("report_id", "citation_id", "case_id"):
            value = item.get(field)
            if value:
                return f"{field}:{value}"
        law_name = item.get("law_name") or item.get("title")
        article_no = item.get("article_no")
        if law_name and article_no:
            return f"law:{law_name}:{article_no}"
        source_id = item.get("source_id")
        if source_id:
            return f"source:{item.get('source_type', '')}:{source_id}"
        try:
            return json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            pass
    return repr(item)


def merge_unique_items(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    """去重追加列表；显式写入空列表时清空，兼容每轮请求的重置逻辑。"""
    return merge_unique_by(left, right, _stable_item_key)


def merge_unique_by(
    left: list[Any] | None,
    right: list[Any] | None,
    key: Callable[[Any], str],
) -> list[Any]:
    """按显式去重键追加列表；同键后写入的值覆盖先写入的值，保持插入顺序。"""
    if right == []:
        return []
    merged = list(left or [])
    positions: dict[str, int] = {}
    for index, item in enumerate(merged):
        positions.setdefault(key(item) or _stable_item_key(item), index)
    for item in right or []:
        item_key = key(item) or _stable_item_key(item)
        if item_key in positions:
            merged[positions[item_key]] = item
        else:
            positions[item_key] = len(merged)
            merged.append(item)
    return merged


def _retrieval_score(item: object) -> float:
    if not isinstance(item, dict):
        return 0.0
    for field in ("rerank_score", "relevance_score", "score", "similarity", "final_score"):
        try:
            if item.get(field) is not None:
                return float(item[field])
        except (TypeError, ValueError):
            continue
    return 0.0


def _merge_top_retrieved(
    left: list[Any] | None,
    right: list[Any] | None,
    *,
    limit: int,
    key: Callable[[Any], str],
) -> list[Any]:
    """Deduplicate and retain only the highest-ranked evidence in working state."""
    merged = merge_unique_by(left, right, key)
    if right == [] or len(merged) <= limit:
        return merged
    ranked = list(enumerate(merged))
    ranked.sort(key=lambda pair: (-_retrieval_score(pair[1]), pair[0]))
    return [item for _, item in ranked[:limit]]


def merge_retrieved_laws(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    """按 evidence_id 口径合并法条证据，防止同一条文在多轮检索中反复累积（P0-4）。

    保留上限取最大档位（长上下文档）的 Top-N：工作态证据池是所有档位共用的，
    若按标准档裁剪，长合同 / 大量类案场景永远拿不到更多证据。真正送进模型的
    条数仍由 ``services.context_builder`` 按本轮档位挑选。
    """
    from services.context_builder import retained_law_top_n

    return _merge_top_retrieved(left, right, limit=retained_law_top_n(), key=law_evidence_key)


def merge_retrieved_cases(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    from services.context_builder import retained_case_top_n

    return _merge_top_retrieved(left, right, limit=retained_case_top_n(), key=case_evidence_key)


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
    """按 ``report_id`` 合并 Agent 报告：同一 report_id 由最新报告覆盖，位置不变。

    局部修复（Repair Router）会让同一个 Agent 就同一 task_id 重新提交报告，
    因此这里必须覆盖而不是丢弃，否则修复结果无法进入核验（P0-6）。
    保留项目原有的「显式写入空列表即清空」语义。
    """
    return merge_unique_items(left, right)  # type: ignore[return-value]


def replace_plan_steps(
    _left: list[PlanStep] | None,
    right: list[PlanStep] | None,
) -> list[PlanStep]:
    """Replace a derived plan view such as ``remaining_steps`` atomically."""
    return [dict(step) for step in right or []]  # type: ignore[misc]


_EVIDENCE_MARKER_HISTORY = 80


def merge_evidence_markers(left: list[str] | None, right: list[str] | None) -> list[str]:
    """记录已归一化的工具消息标识；只保留最近若干条，避免记账字段无界增长。"""
    merged = [str(item) for item in merge_unique_by(left, right, str)]
    return merged[-_EVIDENCE_MARKER_HISTORY:]


def _is_blank_fact(value: Any) -> bool:
    """空字符串、空列表、空字典与 ``None`` 都视为「用户没有提供」。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return not value
    return False


def merge_confirmed_facts(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> dict[str, Any]:
    """跨轮累积用户已确认的事实（§八）。

    澄清补问会分多轮拿到零散事实，因此这里只做「补充或更新」：空值不得覆盖
    已确认事实，否则用户上一轮说过的信息会在下一轮凭空消失。显式写入空字典
    表示重置（换话题或新会话），与项目其他 reducer 的清空语义保持一致。
    """
    if right == {}:
        return {}
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if _is_blank_fact(value):
            continue
        merged[str(key)] = value
    return merged


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
    intent_routed: bool  # 本轮请求是否已经经过独立 Intent Router
    task_complexity: Literal["low", "medium", "high"]  # 复杂度的既有口径，由 Complexity Router 同步

    # 复杂度路由（§九、§P1-1）；execution_mode 决定本轮走固定最小计划还是 Plan-and-Execute
    complexity_level: Literal["simple", "medium", "complex"]  # Complexity Router 的定档结论
    execution_mode: Literal["simple", "plan"]  # simple 跳过 Planner 与整体重排
    needs_case_retrieval: bool  # 是否需要类案检索；默认不查（§五）

    # Supervisor 的路由决策
    supervisor_route: str  # 下一节点或 Agent 名称
    supervisor_reason: str  # 路由原因

    # Plan-and-Execute 规划状态
    plan: Annotated[list[PlanStep], merge_plan_steps]  # 全量结构化计划
    current_step: Optional[str]  # 当前步骤的 step_id
    completed_steps: Annotated[list[PlanStep], merge_plan_steps]  # 已完成步骤
    remaining_steps: Annotated[list[PlanStep], replace_plan_steps]  # 尚待执行步骤
    # §P1-5：本轮计划是否来自 Planner 兜底。兜底保留（否则 Provider 抖动就没有计划），
    # 但必须显式标记并进 Trace，不能让降级计划看起来和模型规划一样可信。
    planner_degraded: bool

    # 检索证据；reducer 按 evidence 口径去重并保持 TopK 有界
    retrieved_laws: Annotated[list[RetrievedLaw], merge_retrieved_laws]
    retrieved_cases: Annotated[list[RetrievedCase], merge_retrieved_cases]

    # Evidence Normalizer 记账：已归一化的工具消息、批次增益与去重统计
    normalized_tool_message_ids: Annotated[list[str], merge_evidence_markers]
    evidence_stats: dict[str, Any]
    evidence_gain: int

    # 专业 Agent 报告；空列表仍可在新请求开始时清空历史报告
    agent_reports: Annotated[list[AgentReport], merge_agent_reports]

    # 事实充分性与澄清补问（§七、§八、§十五）
    # 这些字段不随请求重置，靠 checkpointer 跨轮存活，澄清恢复链路依赖于此。
    case_facts: Optional[CaseFacts]  # Fact Analysis Agent 抽取的结构化事实
    confirmed_facts: Annotated[dict[str, Any], merge_confirmed_facts]  # 跨轮已确认事实
    missing_facts: list[str]  # 仍然缺失的关键事实
    facts_sufficient: bool  # 事实是否足以给出个案结论
    needs_clarification: bool  # 是否需要向用户补问
    clarification_blocking: bool  # 补问是否必须先完成（个案结论 vs 通用说明）
    clarification_questions: list[str]  # 待补问的问题
    clarification_round: int  # 已发起的补问轮次，受 MAX_CLARIFICATION_ROUNDS 限制
    clarification_resumed: bool  # 本轮是否是用户对上一轮补问的回复

    # Verifier 结果与最终引用；verification_result / verified_evidence 是引用的唯一真相源
    verification_result: Optional[VerificationResult]
    verified_evidence: Optional[VerifiedEvidence]
    citations: Annotated[list[Citation], merge_unique_items]
    # §P2：最终答复质量评分，由生成答复的节点通过 services.final_quality 算一次后写入，
    # API 层直接复用，避免出现两套并行的最终评分（§二 问题 12）。
    answer_score: dict[str, Any]

    # 重试和工具调用保护计数；沿用绝对值写入，避免改变现有节点语义
    retry_count: int
    replan_retry_count: int  # Verifier 触发重新规划的次数，最多一次
    verifier_retry_count: int  # 旧 checkpoint 兼容字段；新代码使用 replan_retry_count
    repair_count: int  # Repair Router 已执行的局部修复轮次（P0-5），与重排预算独立
    tool_call_count: int
    # 全请求累计的工具调用次数（MAX_TOOL_CALLS_PER_REQUEST）。只随请求重置，
    # 跨计划步骤、局部修复轮与整体重排一律不归零——否则每次归零都会放开一批新预算。
    tool_call_total: int
    tool_loop_failure: Optional[ToolLoopFailure]
    # §二十二：本轮已执行的检索签名（hash(tool_name + 归一化关键词 + 过滤条件)），
    # 用于拒绝重复检索；随请求与局部修复重置，不做累积合并。
    tool_query_signatures: list[str]
    tool_refresh_allowed: bool  # Repair Router 允许重新检索被质疑的证据（§二十二）

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
    context_build_status: dict[str, Any]  # 最近一次模型调用的分层 token 分配
