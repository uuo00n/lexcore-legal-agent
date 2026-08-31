"""Bounded, isolated Specialist ReAct loop regressions."""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

from agent.nodes.routing import should_continue
from agent.nodes.supervisor import supervisor_agent_node
from agent.state import AgentState
from agent.tool_loop import (
    MAX_TOOL_CALLS,
    TOOL_CALL_LIMIT_ERROR,
    apply_tool_call_budget,
    tool_error_observation,
    tool_limit_observation_node,
)
from agent.tools import (
    CASE_ANALYSIS_TOOLS,
    LEGAL_CONSULT_TOOLS,
    STATUTE_RETRIEVAL_TOOLS,
)


def _response(*names: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": {"query": name}, "id": f"call_{index}"}
            for index, name in enumerate(names)
        ],
    )


def test_specialists_have_exact_dedicated_tool_bindings() -> None:
    assert [tool.name for tool in LEGAL_CONSULT_TOOLS] == [
        "search_law_tool",
        "retrieve_local_law_tool",
    ]
    assert [tool.name for tool in STATUTE_RETRIEVAL_TOOLS] == [
        "search_law_tool",
        "retrieve_local_law_tool",
    ]
    assert [tool.name for tool in CASE_ANALYSIS_TOOLS] == [
        "search_case_tool",
        "search_law_tool",
        "retrieve_local_law_tool",
    ]


def test_budget_counts_calls_and_admits_the_fifth_call() -> None:
    response, count, failure = apply_tool_call_budget(
        _response("search_law_tool", "retrieve_local_law_tool"),
        {"tool_call_count": MAX_TOOL_CALLS - 1},
        agent_name="statute_retrieval_agent",
    )

    assert count == MAX_TOOL_CALLS
    assert failure is None
    assert [call["name"] for call in response.tool_calls] == ["search_law_tool"]
    assert should_continue({
        "messages": [response],
        "tool_call_count": count,
        "tool_loop_failure": None,
    }) == "tools"


def test_sixth_call_is_rejected_and_routed_to_limit_observation() -> None:
    response, count, failure = apply_tool_call_budget(
        _response("search_case_tool"),
        {"tool_call_count": MAX_TOOL_CALLS, "current_step": "step_1"},
        agent_name="case_analysis_agent",
    )

    assert count == MAX_TOOL_CALLS
    assert failure is not None
    assert failure["reason"] == TOOL_CALL_LIMIT_ERROR
    state = {
        "messages": [response],
        "tool_call_count": count,
        "tool_loop_failure": failure,
    }
    assert should_continue(state) == "limit_exceeded"

    result = tool_limit_observation_node(state)
    observation = result["messages"][-1]
    assert isinstance(observation, ToolMessage)
    assert observation.status == "error"
    assert json.loads(observation.content)["retryable"] is False


def test_tool_execution_error_is_retryable_observation() -> None:
    payload = json.loads(tool_error_observation(TimeoutError("upstream timeout")))

    assert payload["status"] == "error"
    assert payload["error"] == "tool_execution_error"
    assert payload["retryable"] is True
    assert "改用" in payload["instruction"]


def test_tool_node_returns_execution_error_to_model_as_observation() -> None:
    @tool
    def failing_search(query: str) -> str:
        """A retrieval stub that always fails."""
        raise TimeoutError(f"timeout: {query}")

    graph = StateGraph(AgentState)
    graph.add_node(
        "tools",
        ToolNode([failing_search], handle_tool_errors=tool_error_observation),
    )
    graph.set_entry_point("tools")
    graph.set_finish_point("tools")
    result = graph.compile().invoke({"messages": [_response("failing_search")]})

    observation = result["messages"][-1]
    payload = json.loads(observation.content)
    assert isinstance(observation, ToolMessage)
    assert observation.status == "error"
    assert payload["retryable"] is True
    assert payload["error_type"] == "TimeoutError"


async def test_supervisor_marks_over_budget_plan_step_failed_without_retry() -> None:
    failure = {
        "agent_name": "statute_retrieval_agent",
        "task_id": "step_1",
        "reason": TOOL_CALL_LIMIT_ERROR,
        "message": "任务工具调用次数已达到上限 5",
        "tool_call_count": 5,
        "max_tool_calls": 5,
    }
    result = await supervisor_agent_node({
        "plan": [{
            "step_id": "step_1",
            "task_type": "statute_retrieval",
            "description": "检索法规",
            "assigned_agent": "statute_retrieval_agent",
            "status": "running",
        }],
        "current_step": "step_1",
        "retry_count": 0,
        "tool_call_count": 5,
        "tool_loop_failure": failure,
    })

    assert result["plan"][0]["status"] == "failed"
    assert result["plan"][0]["result"]["reason"] == TOOL_CALL_LIMIT_ERROR
    assert result["retry_count"] == 0
    assert result["supervisor_route"] == "verify"
