"""Bounded, isolated Specialist ReAct loop regressions."""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

from agent.nodes.routing import should_continue
from agent.nodes.supervisor import supervisor_agent_node
from agent.state import AgentState
from agent.tool_loop import (
    EVIDENCE_LAW_TARGET,
    MAX_TOOL_CALLS_PER_AGENT,
    MAX_TOOL_CALLS_PER_REQUEST,
    STOP_DUPLICATE_QUERY,
    STOP_EVIDENCE_TARGET_REACHED,
    STOP_NO_EVIDENCE_GAIN,
    STOP_REQUEST_BUDGET_EXHAUSTED,
    TOOL_CALL_LIMIT_ERROR,
    admit_tool_calls,
    apply_tool_call_budget,
    evaluate_tool_stop,
    query_signature,
    request_budget_exhausted,
    tool_call_signature,
    tool_error_observation,
    tool_limit_observation_node,
)
from agent.tools import (
    CASE_ANALYSIS_TOOLS,
    LEGAL_CONSULT_TOOLS,
    STATUTE_RETRIEVAL_TOOLS,
)
from services.cache.keys import DIGEST_LENGTH


def _response(*names: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": {"query": name}, "id": f"call_{index}"}
            for index, name in enumerate(names)
        ],
    )


def _laws(count: int) -> list[dict[str, str]]:
    return [
        {
            "law_name": "劳动合同法",
            "article_no": f"第{index + 1}条",
            "content": "用人单位应当及时足额支付劳动报酬。",
        }
        for index in range(count)
    ]


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


def test_budget_counts_calls_and_admits_the_last_allowed_call() -> None:
    response, count, total, failure = apply_tool_call_budget(
        _response("search_law_tool", "retrieve_local_law_tool"),
        {"tool_call_count": MAX_TOOL_CALLS_PER_AGENT - 1},
        agent_name="statute_retrieval_agent",
    )

    assert count == MAX_TOOL_CALLS_PER_AGENT
    assert total == 1
    assert failure is None
    assert [call["name"] for call in response.tool_calls] == ["search_law_tool"]
    assert should_continue({
        "messages": [response],
        "tool_call_count": count,
        "tool_loop_failure": None,
    }) == "tools"


def test_call_over_budget_is_rejected_and_routed_to_limit_observation() -> None:
    response, count, total, failure = apply_tool_call_budget(
        _response("search_case_tool"),
        {"tool_call_count": MAX_TOOL_CALLS_PER_AGENT, "current_step": "step_1"},
        agent_name="case_analysis_agent",
    )

    assert count == MAX_TOOL_CALLS_PER_AGENT
    assert total == 0
    assert failure is not None
    assert failure["reason"] == TOOL_CALL_LIMIT_ERROR
    assert failure["max_tool_calls"] == MAX_TOOL_CALLS_PER_AGENT
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


def test_query_signature_ignores_wording_order_and_injected_runtime_args() -> None:
    """§二十二：签名只取工具名 + 归一化关键词 + 过滤条件，且只落摘要。"""
    left = query_signature("search_law_tool", {"query": "拖欠工资 经济补偿", "trace_id": "trace-a"})
    right = query_signature("search_law_tool", {"query": " 经济补偿，拖欠工资 ", "trace_id": "trace-b"})

    assert left == right
    assert len(left) == DIGEST_LENGTH
    assert "拖欠工资" not in left


def test_query_signature_separates_tools_and_filters() -> None:
    base = query_signature("search_law_tool", {"query": "拖欠工资"})

    assert query_signature("search_case_tool", {"query": "拖欠工资"}) != base
    assert query_signature("search_law_tool", {"query": "拖欠工资", "top_k": 5}) != base
    assert tool_call_signature({"name": "search_law_tool", "args": {"query": "拖欠工资"}}) == base


def test_duplicate_query_signature_stops_the_loop() -> None:
    """§二十二：同一轮内重复同一检索签名不再放行。"""
    call = {"name": "search_law_tool", "args": {"query": "拖欠工资 经济补偿"}, "id": "call_0"}
    decision = evaluate_tool_stop(
        {
            "tool_call_count": 1,
            "tool_query_signatures": [tool_call_signature(call)],
            "retrieved_laws": _laws(1),
        },
        [call],
    )

    assert decision.stop is True
    assert decision.reason == STOP_DUPLICATE_QUERY
    assert decision.duplicates == ("search_law_tool",)


def test_zero_evidence_gain_stops_the_loop_immediately() -> None:
    """§P1-3、§三十 用例 4：上一轮检索没带来新证据就立即停止。"""
    decision = evaluate_tool_stop(
        {"tool_call_count": 1, "evidence_gain": 0, "retrieved_laws": _laws(1)},
        [{"name": "search_law_tool", "args": {"query": "换个说法再查一次"}, "id": "call_0"}],
    )

    assert decision.stop is True
    assert decision.reason == STOP_NO_EVIDENCE_GAIN
    assert decision.detail["evidence_gain"] == 0


def test_first_retrieval_is_not_blocked_by_a_stale_evidence_gain() -> None:
    """刚接手步骤（tool_call_count == 0）时不该被上一步的陈旧增益掐掉首次检索。"""
    decision = evaluate_tool_stop(
        {"tool_call_count": 0, "evidence_gain": 0},
        [{"name": "search_law_tool", "args": {"query": "拖欠工资"}, "id": "call_0"}],
    )

    assert decision.stop is False
    assert [call["name"] for call in decision.admitted] == ["search_law_tool"]


def test_evidence_target_reached_stops_the_loop() -> None:
    decision = evaluate_tool_stop(
        {"tool_call_count": 1, "retrieved_laws": _laws(EVIDENCE_LAW_TARGET)},
        [{"name": "search_law_tool", "args": {"query": "再补几条"}, "id": "call_0"}],
    )

    assert decision.stop is True
    assert decision.reason == STOP_EVIDENCE_TARGET_REACHED
    assert decision.detail["law_count"] == EVIDENCE_LAW_TARGET


def test_repair_refresh_waives_duplicate_and_target_stops() -> None:
    """§二十二：Repair Router 要求刷新时，允许重跑被质疑条文的同一签名检索。"""
    call = {"name": "search_law_tool", "args": {"query": "拖欠工资"}, "id": "call_0"}
    decision = evaluate_tool_stop(
        {
            "tool_call_count": 1,
            "tool_query_signatures": [tool_call_signature(call)],
            "retrieved_laws": _laws(EVIDENCE_LAW_TARGET),
            "tool_refresh_allowed": True,
        },
        [call],
    )

    assert decision.stop is False
    assert [item["name"] for item in decision.admitted] == ["search_law_tool"]


def test_repair_refresh_still_stops_without_evidence_gain() -> None:
    decision = evaluate_tool_stop(
        {"tool_call_count": 1, "evidence_gain": 0, "tool_refresh_allowed": True},
        [{"name": "search_law_tool", "args": {"query": "拖欠工资"}, "id": "call_0"}],
    )

    assert decision.stop is True
    assert decision.reason == STOP_NO_EVIDENCE_GAIN


def test_admit_tool_calls_records_signatures_and_drops_only_the_duplicate() -> None:
    seen = query_signature("search_law_tool", {"query": "search_law_tool"})
    step = admit_tool_calls(
        _response("search_law_tool", "retrieve_local_law_tool"),
        {"tool_call_count": 0, "tool_query_signatures": [seen]},
        agent_name="statute_retrieval_agent",
    )

    assert step.continue_loop is True
    assert step.detail["duplicate_tools"] == ["search_law_tool"]
    admitted = step.updates["messages"][0]
    assert [call["name"] for call in admitted.tool_calls] == ["retrieve_local_law_tool"]
    assert step.updates["tool_call_count"] == 1
    assert step.updates["tool_loop_failure"] is None
    assert step.updates["tool_query_signatures"] == [
        seen,
        query_signature("retrieve_local_law_tool", {"query": "retrieve_local_law_tool"}),
    ]


def test_admit_tool_calls_soft_stop_leaves_the_tool_loop_without_failure() -> None:
    call = {"name": "search_law_tool", "args": {"query": "拖欠工资"}, "id": "call_0"}
    step = admit_tool_calls(
        AIMessage(content="", tool_calls=[call]),
        {"tool_call_count": 1, "tool_query_signatures": [tool_call_signature(call)]},
        agent_name="statute_retrieval_agent",
    )

    assert step.continue_loop is False
    assert step.updates is None
    assert step.stop_reason == STOP_DUPLICATE_QUERY


def test_admit_tool_calls_without_tool_calls_is_a_plain_finish() -> None:
    step = admit_tool_calls(
        AIMessage(content="最终结论"),
        {"tool_call_count": 1},
        agent_name="legal_consult_agent",
    )

    assert step.continue_loop is False
    assert step.stop_reason == ""


def test_request_budget_exhausted_stops_as_soft_stop_without_failure() -> None:
    """全请求预算耗尽是软停止：不写 tool_loop_failure，Agent 用已有证据出报告。"""
    state = {"tool_call_count": 0, "tool_call_total": MAX_TOOL_CALLS_PER_REQUEST}
    decision = evaluate_tool_stop(state, [
        {"name": "search_law_tool", "args": {"query": "拖欠工资"}, "id": "call_0"},
    ])

    assert decision.stop is True
    assert decision.reason == STOP_REQUEST_BUDGET_EXHAUSTED
    assert decision.detail["max_tool_calls_per_request"] == MAX_TOOL_CALLS_PER_REQUEST
    assert request_budget_exhausted(state) is True

    step = admit_tool_calls(_response("search_law_tool"), state, agent_name="statute_retrieval_agent")

    assert step.continue_loop is False
    assert step.updates is None
    assert step.stop_reason == STOP_REQUEST_BUDGET_EXHAUSTED


def test_request_budget_is_not_waived_by_repair_refresh() -> None:
    """刷新可以绕过「证据够用」和「重复签名」，但绕不过成本上限，否则预算从修复路径漏出去。"""
    decision = evaluate_tool_stop(
        {
            "tool_call_count": 0,
            "tool_call_total": MAX_TOOL_CALLS_PER_REQUEST,
            "tool_refresh_allowed": True,
        },
        [{"name": "search_law_tool", "args": {"query": "拖欠工资"}, "id": "call_0"}],
    )

    assert decision.stop is True
    assert decision.reason == STOP_REQUEST_BUDGET_EXHAUSTED


def test_request_budget_accumulates_across_plan_steps() -> None:
    """单任务计数每步归零，累计值不归零：只剩一次额度时只放行一次调用。"""
    step = admit_tool_calls(
        _response("search_law_tool", "search_case_tool"),
        {"tool_call_count": 0, "tool_call_total": MAX_TOOL_CALLS_PER_REQUEST - 1},
        agent_name="case_analysis_agent",
    )

    assert step.continue_loop is True
    admitted = step.updates["messages"][0]
    assert [call["name"] for call in admitted.tool_calls] == ["search_law_tool"]
    assert step.updates["tool_call_count"] == 1
    assert step.updates["tool_call_total"] == MAX_TOOL_CALLS_PER_REQUEST
    assert step.updates["tool_loop_failure"] is None


def test_request_budget_is_the_binding_constraint_before_the_agent_limit() -> None:
    """全请求额度先见底时按软停止裁空整批调用，不冒充单任务硬上限。"""
    response, count, total, failure = apply_tool_call_budget(
        _response("search_law_tool"),
        {"tool_call_count": 0, "tool_call_total": MAX_TOOL_CALLS_PER_REQUEST},
        agent_name="statute_retrieval_agent",
    )

    assert failure is None
    assert count == 0
    assert total == MAX_TOOL_CALLS_PER_REQUEST
    assert list(response.tool_calls) == []


async def test_soft_stop_makes_specialist_report_from_existing_evidence(monkeypatch) -> None:
    """§P1-2、§P1-3：软停止不是执行失败，Agent 直接用手上的证据出报告。"""

    class _ToolHungryLLM:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            return _response("search_law_tool")

    monkeypatch.setattr("agent.nodes.get_llm", lambda **kwargs: _ToolHungryLLM())
    monkeypatch.setattr("agent.nodes.supports_tools", lambda provider=None: True)

    from agent.agents.statute_retrieval_agent import statute_retrieval_agent_node

    result = await statute_retrieval_agent_node({
        "messages": [HumanMessage(content="公司拖欠工资怎么办")],
        "retrieved_laws": _laws(EVIDENCE_LAW_TARGET),
        "tool_call_count": 1,
    })

    assert "messages" not in result
    assert "tool_loop_failure" not in result
    report = result["agent_reports"][0]
    assert report["status"] == "report_ready"
    assert len(report["findings"]["statutes"]) == EVIDENCE_LAW_TARGET


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
        "message": f"任务工具调用次数已达到上限 {MAX_TOOL_CALLS_PER_AGENT}",
        "tool_call_count": MAX_TOOL_CALLS_PER_AGENT,
        "max_tool_calls": MAX_TOOL_CALLS_PER_AGENT,
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
        "tool_call_count": MAX_TOOL_CALLS_PER_AGENT,
        "tool_loop_failure": failure,
    })

    assert result["plan"][0]["status"] == "failed"
    assert result["plan"][0]["result"]["reason"] == TOOL_CALL_LIMIT_ERROR
    assert result["retry_count"] == 0
    assert result["supervisor_route"] == "verify"
