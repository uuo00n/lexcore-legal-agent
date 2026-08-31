"""Agent retrieval Tool Layer contracts and guardrails."""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

from agent.state import AgentState
from agent.tools import (
    CASE_ANALYSIS_TOOLS,
    STATUTE_RETRIEVAL_TOOLS,
    search_case_tool,
    search_law_tool,
)
from agent.tools.schemas import MAX_TOOL_OUTPUT_CHARS
from services.delilegal.exceptions import DelilegalAuthenticationError
from services.delilegal.schemas import (
    CaseSearchResponse,
    CaseSearchResult,
    LawSearchResponse,
    LawSearchResult,
)


def _tool_state(name: str, args: dict, trace_id: str = "trace-tool-test") -> dict:
    return {
        "trace_id": trace_id,
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{"name": name, "args": args, "id": "call-1", "type": "tool_call"}],
            )
        ],
    }


def _payload(result: dict) -> tuple[ToolMessage, dict]:
    message = result["messages"][-1]
    return message, json.loads(message.content)


async def _invoke_tool(retrieval_tool, state: dict) -> dict:
    graph = StateGraph(AgentState)
    graph.add_node("tools", ToolNode([retrieval_tool]))
    graph.set_entry_point("tools")
    graph.set_finish_point("tools")
    return await graph.compile().ainvoke(state)


def test_retrieval_tool_schemas_hide_trace_and_cap_top_k():
    for retrieval_tool in (search_law_tool, search_case_tool):
        schema = retrieval_tool.tool_call_schema.model_json_schema()
        assert "trace_id" not in schema["properties"]
        assert schema["properties"]["top_k"]["default"] == 5
        assert schema["properties"]["top_k"]["maximum"] == 5


def test_tool_descriptions_define_when_to_call_and_when_not_to_call():
    assert "当" in search_law_tool.description
    assert "不要" in search_law_tool.description
    assert "已有充分法规结果" in search_law_tool.description
    assert "仅当" in search_case_tool.description
    assert "不要" in search_case_tool.description
    assert "已有充分类案" in search_case_tool.description


def test_specialist_agents_prioritize_their_primary_retrieval_tool():
    assert STATUTE_RETRIEVAL_TOOLS[0].name == "search_law_tool"
    assert CASE_ANALYSIS_TOOLS[0].name == "search_case_tool"


async def test_law_tool_uses_service_client_injects_trace_and_bounds_results(monkeypatch):
    seen: dict = {}
    events: list[tuple[str, str, dict]] = []

    class FakeClient:
        def __init__(self, *, trace_id=None):
            seen["trace_id"] = trace_id

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def search_laws(self, request):
            seen["request"] = request
            return LawSearchResponse(
                query_id="law-query",
                total_count=9,
                items=[
                    LawSearchResult(
                        id=f"law-{index}",
                        title="劳动合同法",
                        law_name="劳动合同法",
                        article="第四十六条",
                        content="第四十六条 用人单位依法应当支付经济补偿。" * 80,
                    )
                    for index in range(9)
                ],
            )

    monkeypatch.setattr("agent.tools.law_search.DelilegalClient", FakeClient)
    monkeypatch.setattr(
        "agent.tools._runtime.record_trace_event",
        lambda trace_id, event_type, *, name="", payload=None: events.append(
            (trace_id, event_type, payload or {})
        ),
    )

    result = await _invoke_tool(
        search_law_tool,
        _tool_state("search_law_tool", {"query": "经济补偿"})
    )
    _message, payload = _payload(result)

    assert seen["trace_id"] == "trace-tool-test"
    assert seen["request"].page_size == 5
    assert payload["trace_id"] == "trace-tool-test"
    assert payload["success"] is True
    assert payload["latency_ms"] >= 0
    assert payload["result_count"] <= 5
    assert payload["truncated"] is True
    assert len(json.dumps(payload["results"], ensure_ascii=False)) <= MAX_TOOL_OUTPUT_CHARS + 500
    assert events[-1][1] == "tool_end"
    assert events[-1][2]["success"] is True


async def test_case_tool_calls_service_with_top_k_and_compresses_documents(monkeypatch):
    seen: dict = {}

    class FakeClient:
        def __init__(self, *, trace_id=None):
            seen["trace_id"] = trace_id

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def search_cases(self, request):
            seen["request"] = request
            return CaseSearchResponse(
                query_id="case-query",
                total_count=1,
                items=[
                    CaseSearchResult(
                        id="case-1",
                        title="押金返还纠纷",
                        court="某法院",
                        judgment="返还押金",
                        content=(
                            "经审理查明：租赁期满后承租人已经交房。\n"
                            "本院认为：出租人无正当理由扣留押金。\n"
                            "判决如下：返还押金。"
                        ),
                    )
                ],
            )

    monkeypatch.setattr("agent.tools.case_search.DelilegalClient", FakeClient)
    monkeypatch.setattr("agent.tools._runtime.record_trace_event", lambda *_args, **_kwargs: None)

    result = await _invoke_tool(
        search_case_tool,
        _tool_state("search_case_tool", {"long_text": "租期届满，房东拒绝返还押金", "top_k": 3})
    )
    _message, payload = _payload(result)

    assert seen["request"].page_size == 3
    assert payload["source_type"] == "delilegal_case"
    assert payload["result_count"] == 1
    assert "content" not in payload["results"][0]
    assert payload["results"][0]["court_reasoning"]


async def test_delilegal_error_becomes_safe_agent_tool_error(monkeypatch):
    events: list[dict] = []

    class FailingClient:
        def __init__(self, *, trace_id=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def search_laws(self, _request):
            raise DelilegalAuthenticationError("bad appid=my-app secret=my-secret")

    monkeypatch.setattr("agent.tools.law_search.DelilegalClient", FailingClient)
    monkeypatch.setattr(
        "agent.tools._runtime.record_trace_event",
        lambda _trace_id, _event_type, *, name="", payload=None: events.append(payload or {}),
    )

    result = await _invoke_tool(
        search_law_tool,
        _tool_state("search_law_tool", {"query": "劳动合同解除"}, "trace-error")
    )
    message, payload = _payload(result)

    assert message.status == "error"
    assert payload["status"] == "error"
    assert payload["success"] is False
    assert payload["error"]["code"] == "authentication_failed"
    assert payload["trace_id"] == "trace-error"
    assert "my-app" not in message.content
    assert "my-secret" not in message.content
    assert events[-1]["success"] is False
