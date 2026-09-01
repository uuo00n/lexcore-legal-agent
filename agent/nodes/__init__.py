"""Compatibility facade for LangGraph node functions.

Implementations are split by responsibility, while this package preserves the
former ``agent.nodes`` import surface used by the graph and external callers.
"""

# Preserve legacy dependency patch points used by integrations and tests.
from services.case_retrieval import search_similar_cases
from services.llm import get_llm, supports_tools
from services.model_routing import select_model_route
from services.supervisor import route_user_request_with_llm

from agent.agents.contract_agent import contract_agent_node
from agent.agents.fact_agent import case_analysis_agent_node, fact_agent_node, fact_check_node
from agent.agents.statute_retrieval_agent import statute_retrieval_agent_node
from agent.agents.legal_consult_agent import (
    _build_legal_agent_report,
    _extract_json_object,
    _format_law_sources,
    _guard_law_citations,
    _has_used_local_law_tool,
    _law_basis_from_retrieval,
    _law_key,
    _legal_consult_tools_for_state,
    _limit_tool_calls,
    _normalize_law_name,
    agent_node,
    legal_consult_agent_node,
)
from agent.nodes.context import (
    context_compaction_node,
    latest_human_message as _latest_human_message,
    record_trace_event as _record_trace_event,
)
from agent.nodes.query import query_rewrite_node
from agent.nodes.document import DOC_PREFIX, inject_doc_node
from agent.nodes.memory import memory_node
from agent.nodes.planner import planner_node
from agent.nodes.routing import (
    MAX_TOOL_CALLS,
    collect_retrieved_laws,
    should_after_fact_check,
    should_after_planner,
    should_after_supervisor,
    should_enter_planner,
    should_after_verifier,
    should_continue,
    should_execute_next,
)
from agent.nodes.supervisor import intent_router_node, supervisor_agent_node
from agent.nodes.answer import answer_generator_node
from agent.nodes.verifier import (
    result_verifier_node,
    verifier_node,
    verify_plan_results,
)
from agent.tool_loop import tool_limit_observation_node

__all__ = [
    "MAX_TOOL_CALLS",
    "agent_node",
    "answer_generator_node",
    "collect_retrieved_laws",
    "contract_agent_node",
    "case_analysis_agent_node",
    "context_compaction_node",
    "fact_agent_node",
    "fact_check_node",
    "inject_doc_node",
    "intent_router_node",
    "legal_consult_agent_node",
    "statute_retrieval_agent_node",
    "memory_node",
    "planner_node",
    "query_rewrite_node",
    "should_after_fact_check",
    "should_after_planner",
    "should_after_supervisor",
    "should_after_verifier",
    "should_enter_planner",
    "should_continue",
    "should_execute_next",
    "supervisor_agent_node",
    "tool_limit_observation_node",
    "result_verifier_node",
    "verifier_node",
    "verify_plan_results",
]
