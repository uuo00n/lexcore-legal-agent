from __future__ import annotations

from fastapi import Request
from starlette.responses import Response

from agent.graph import _observed_node
from main import bind_request_trace
from infrastructure.operational_store import InMemoryOperationalStore, init_operational_store
from services.checkpoint import reset_for_tests
from services.observability import create_trace, get_trace


def setup_function():
    reset_for_tests()
    init_operational_store(InMemoryOperationalStore())


def teardown_function():
    reset_for_tests()


async def test_fastapi_middleware_generates_one_trace_id_and_response_header():
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/chat",
            "raw_path": b"/api/chat",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
            "state": {},
        }
    )
    captured: dict[str, str] = {}

    async def call_next(inner_request: Request) -> Response:
        captured["trace_id"] = inner_request.state.trace_id
        return Response(status_code=204)

    response = await bind_request_trace(request, call_next)

    assert len(captured["trace_id"]) == 16
    assert response.headers["X-Trace-ID"] == captured["trace_id"]


async def test_langgraph_node_records_context_latency_and_retrieval_count(
):
    create_trace("trace-node", "thread-node", "检索劳动合同法")

    async def node(_state):
        return {"retrieved_laws": [{"article_no": "第三十九条"}]}

    observed = _observed_node(
        "statute_retrieval_agent",
        node,
        agent_name="statute_retrieval_agent",
    )
    result = await observed(
        {
            "trace_id": "trace-node",
            "thread_id": "thread-node",
            "retry_count": 1,
        },
        {"configurable": {"trace_id": "trace-node", "thread_id": "thread-node"}},
    )

    assert result["retrieved_laws"]
    event = get_trace("trace-node")["events"][0]
    assert event["event_type"] == "graph_node"
    assert event["payload"]["node_name"] == "statute_retrieval_agent"
    assert event["payload"]["agent_name"] == "statute_retrieval_agent"
    assert event["payload"]["retrieval_count"] == 1
    assert event["payload"]["retry_count"] == 1
    assert event["payload"]["latency_ms"] >= 0
    assert event["payload"]["success"] is True
