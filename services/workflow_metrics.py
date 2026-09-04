"""工作流指标（§二十五）。

§二十六 的目标只有变成指标才可验收：简单问答的时延、单 Agent 工具调用次数、
进模型的证据条数、引用可追溯比例、局部修复与整体重排的比例。这些数字散落在
各个节点里，如果每个节点各自拼 ``inc_counter`` 的名字与标签，指标名很快就会漂移
（§三十一「Service/Node 分层」）。这里把每个指标收成一个显式函数：节点只描述
「发生了什么」，指标名与标签口径由本模块独占。

命名统一使用 ``legal_workflow_`` 前缀，与 ``api/chat.py`` 既有的
``legal_chat_*`` 请求级指标区分开：前者按工作流阶段聚合，后者按 HTTP 请求聚合。
"""
from __future__ import annotations

from services.metrics import inc_counter, observe


def _flag(value: object) -> str:
    """布尔标签统一成 ``true`` / ``false``，避免出现 ``True`` 与 ``true`` 两种值。"""
    return "true" if bool(value) else "false"


def _text(value: object, default: str = "unknown") -> str:
    """标签值不允许为空：空字符串在 Prometheus 里无法与「未上报」区分。"""
    text = str(value or "").strip()
    return text or default


# ─── 节点执行（由 observed_node 统一上报，覆盖全部 Graph 节点）────────────────
def record_node_execution(node_name: str, *, latency_ms: float, success: bool) -> None:
    """单个 Graph 节点的时延与成败。"""
    node = _text(node_name, "unknown_node")
    observe("legal_workflow_node_latency_ms", float(latency_ms), {"node": node})
    inc_counter(
        "legal_workflow_node_total",
        {"node": node, "status": "success" if success else "error"},
    )


# ─── 路由决策（§P1-1、§五）────────────────────────────────────────────────
def record_complexity_route(
    *,
    complexity_level: str,
    execution_mode: str,
    needs_case_retrieval: bool,
) -> None:
    """复杂度定档结果；简单路径占比就是从这里算的。"""
    inc_counter(
        "legal_workflow_route_total",
        {
            "complexity_level": _text(complexity_level),
            "execution_mode": _text(execution_mode),
            "needs_case_retrieval": _flag(needs_case_retrieval),
        },
    )


def record_clarification(outcome: str, *, blocking: bool = False) -> None:
    """澄清补问的发起与恢复（``required`` / ``resumed``）。"""
    inc_counter(
        "legal_workflow_clarification_total",
        {"outcome": _text(outcome), "blocking": _flag(blocking)},
    )


def record_planner_degraded() -> None:
    """§P1-5：Planner 兜底次数，用于判断降级是否已经成为常态。"""
    inc_counter("legal_workflow_planner_degraded_total")


# ─── 工具循环（§P1-2、§P1-3、§二十二）────────────────────────────────────
def record_tool_calls(agent_name: str, count: int) -> None:
    """本次放行的工具调用数；配合 ``_total`` 可算出单 Agent 平均调用次数。"""
    if count <= 0:
        return
    agent = _text(agent_name, "unknown_agent")
    inc_counter("legal_workflow_tool_calls_total", {"agent": agent}, float(count))
    observe("legal_workflow_agent_tool_calls", float(count), {"agent": agent})


def record_tool_loop_stopped(agent_name: str, reason: str) -> None:
    """软停止原因分布：证据够用 / 重复检索 / 零增益。"""
    inc_counter(
        "legal_workflow_tool_loop_stopped_total",
        {"agent": _text(agent_name, "unknown_agent"), "reason": _text(reason)},
    )


# ─── 证据归一化（§P0-3、§P0-4、§P1-7）────────────────────────────────────
def record_evidence_normalized(
    *,
    law_count: int,
    case_count: int,
    dropped_count: int,
    evidence_gain: int,
) -> None:
    """归一化后真正进入 State 的证据规模，以及本批次的增益。"""
    observe("legal_workflow_evidence_kept", float(law_count), {"kind": "law"})
    observe("legal_workflow_evidence_kept", float(case_count), {"kind": "case"})
    observe("legal_workflow_evidence_gain", float(evidence_gain))
    if dropped_count > 0:
        inc_counter("legal_workflow_evidence_dropped_total", None, float(dropped_count))


# ─── 核验与修复（§P0-1、§P0-5、§P1-6）────────────────────────────────────
def record_verification(
    *,
    passed: bool,
    degraded: bool,
    citation_verified: int,
    citation_unsupported: int,
) -> None:
    """核验结论与引用可追溯比例（§二十六「100% 引用可追溯」的分子分母）。"""
    inc_counter(
        "legal_workflow_verification_total",
        {"result": "passed" if passed else "failed", "degraded": _flag(degraded)},
    )
    if degraded:
        inc_counter("legal_workflow_verification_degraded_total")
    if citation_verified > 0:
        inc_counter(
            "legal_workflow_citations_total",
            {"status": "verified"},
            float(citation_verified),
        )
    if citation_unsupported > 0:
        inc_counter(
            "legal_workflow_citations_total",
            {"status": "unsupported"},
            float(citation_unsupported),
        )


def record_repair(targets: list[str]) -> None:
    """局部修复按目标计数；``repair`` 与 ``replan`` 的比例是「不默认整体重跑」的证据。"""
    for target in targets or []:
        inc_counter("legal_workflow_repair_total", {"target": _text(target)})
    inc_counter("legal_workflow_recovery_total", {"strategy": "repair"})


def record_replan(*, skipped: bool = False) -> None:
    """整体重排；``skipped`` 表示简单路径直接放弃重排（§P1-1）。"""
    inc_counter(
        "legal_workflow_recovery_total",
        {"strategy": "replan_skipped" if skipped else "replan"},
    )


# ─── 最终答复（§P2）──────────────────────────────────────────────────────
def record_answer(outcome: str, *, attempts: int = 1) -> None:
    """答复来源：模型首稿、重写稿还是确定性重建。"""
    inc_counter("legal_workflow_answer_total", {"outcome": _text(outcome)})
    observe("legal_workflow_answer_attempts", float(max(1, attempts)))


def record_workflow_latency(elapsed_ms: float, *, execution_mode: str, status: str) -> None:
    """端到端时延，按执行模式分桶；§二十六 的简单问答时延目标看这条。"""
    observe(
        "legal_workflow_latency_ms",
        float(elapsed_ms),
        {"execution_mode": _text(execution_mode), "status": _text(status)},
    )
