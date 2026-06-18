"""LangGraph StateGraph 构建 —— 定义 ReAct 循环的图拓扑。"""
from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from agent.nodes import (
    collect_retrieved_laws,
    contract_agent_node,
    context_compaction_node,
    fact_agent_node,
    inject_doc_node,
    legal_consult_agent_node,
    memory_node,
    should_after_supervisor,
    should_continue,
    supervisor_agent_node,
)
from agent.state import AgentState
from agent.tools import ALL_TOOLS


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> Any:
    """
    函数作用：
        构建 LangGraph 状态图。
    输入参数：
        - checkpointer: BaseCheckpointSaver | None，默认值 None
    输出参数：
        - Any
    """
    graph = StateGraph(AgentState)

    graph.add_node("context_compaction", context_compaction_node)
    graph.add_node("memory", memory_node)
    graph.add_node("inject_doc", inject_doc_node)
    graph.add_node("supervisor_agent", supervisor_agent_node)
    graph.add_node("fact_agent", fact_agent_node)
    graph.add_node("contract_agent", contract_agent_node)
    graph.add_node("legal_consult_agent", legal_consult_agent_node)
    graph.add_node("tools", ToolNode(ALL_TOOLS))
    graph.add_node("collect_laws", collect_retrieved_laws)

    graph.set_entry_point("context_compaction")
    graph.add_edge("context_compaction", "memory")
    graph.add_edge("memory", "inject_doc")
    graph.add_edge("inject_doc", "supervisor_agent")
    graph.add_conditional_edges(
        "supervisor_agent",
        should_after_supervisor,
        {
            "fact_agent": "fact_agent",
            "contract_agent": "contract_agent",
            "legal_consult_agent": "legal_consult_agent",
            "end": END,
        },
    )
    graph.add_edge("fact_agent", "supervisor_agent")
    graph.add_edge("contract_agent", "supervisor_agent")
    graph.add_conditional_edges(
        "legal_consult_agent",
        should_continue,
        {"tools": "tools", "end": "supervisor_agent"},
    )
    graph.add_edge("tools", "collect_laws")
    graph.add_edge("collect_laws", "legal_consult_agent")

    return graph.compile(checkpointer=checkpointer)
