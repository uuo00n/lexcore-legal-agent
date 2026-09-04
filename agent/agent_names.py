"""§四 的 Agent 名称登记表：规范职责名、图内节点名与旧名之间的唯一映射。

§四 把执行单元的职责名定为 ``fact_analysis_agent`` / ``law_retrieval_agent`` /
``case_retrieval_agent`` / ``legal_reasoning_agent``，同时允许保留兼容别名。本项目采用
「别名层」而不是物理重命名：

* **发出的名字**保持不变——图内节点名、报告 ``agent_name``、trace 事件的 ``name``、
  旧 checkpoint 里的 ``assigned_agent`` 一律沿用现有取值，前端与 Admin 时间线不受影响；
* **接受的名字**在所有做分派的地方同时支持两套词表，并统一在这里解析成节点名。

这样既落实 §四 的职责口径，又不违反「禁止大范围重命名无关模块 / 为优雅破坏现有接口」
与「若必须修改接口须提供兼容层」。本模块只依赖标准库，任何层都可以安全导入。
"""
from __future__ import annotations


# §四 的核心 Agent 职责名。``supervisor_agent`` 只做调度，不是可分派的执行单元。
SUPERVISOR_AGENT = "supervisor_agent"
FACT_ANALYSIS_AGENT = "fact_analysis_agent"
LAW_RETRIEVAL_AGENT = "law_retrieval_agent"
CASE_RETRIEVAL_AGENT = "case_retrieval_agent"
LEGAL_REASONING_AGENT = "legal_reasoning_agent"

# Supervisor 可分派的图内节点名；也是报告 ``agent_name`` 与 trace 里出现的取值。
SPECIALIST_NODES: tuple[str, ...] = (
    "case_analysis_agent",
    "statute_retrieval_agent",
    "case_retrieval_agent",
    "legal_consult_agent",
)

# 规范名 → 图内节点名。``case_retrieval_agent`` 有自己的执行节点（§五），所以是恒等
# 映射：类案检索不能再借用事实分析 Agent，否则「只在需要时查类案」这条约束既无法在
# 计划里表达，也无法在修复路由里精确重跑。
AGENT_NODE_ALIASES: dict[str, str] = {
    FACT_ANALYSIS_AGENT: "case_analysis_agent",
    LAW_RETRIEVAL_AGENT: "statute_retrieval_agent",
    CASE_RETRIEVAL_AGENT: "case_retrieval_agent",
    LEGAL_REASONING_AGENT: "legal_consult_agent",
}

# 计划步骤、修复路由与 Supervisor 决策里允许出现的全部 Agent 名（新旧词表并存）。
DISPATCHABLE_AGENTS: frozenset[str] = frozenset(SPECIALIST_NODES) | frozenset(AGENT_NODE_ALIASES)


def agent_node(name: str) -> str:
    """把任意 Agent 名解析成图内节点名；未登记的名字原样返回，由调用方判断合法性。"""
    text = str(name or "")
    return AGENT_NODE_ALIASES.get(text, text)


def same_agent(left: str, right: str) -> bool:
    """两个名字是否指向同一执行单元；用于跨新旧词表比较计划步骤与专家报告。"""
    return bool(left) and bool(right) and agent_node(left) == agent_node(right)


__all__ = [
    "AGENT_NODE_ALIASES",
    "CASE_RETRIEVAL_AGENT",
    "DISPATCHABLE_AGENTS",
    "FACT_ANALYSIS_AGENT",
    "LAW_RETRIEVAL_AGENT",
    "LEGAL_REASONING_AGENT",
    "SPECIALIST_NODES",
    "SUPERVISOR_AGENT",
    "agent_node",
    "same_agent",
]
