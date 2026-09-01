"""FastMCP 只作为 Service 能力暴露层的架构回归测试。"""
from __future__ import annotations

import json
from pathlib import Path

from agent.tools.schemas import CaseSearchToolInput, LawSearchToolInput
from services.search import (
    CaseSearchParams,
    LawSearchParams,
    SearchServiceResult,
)


ROOT = Path(__file__).parents[1]


def test_langgraph_tools_do_not_depend_on_mcp_client():
    for path in (ROOT / "agent" / "tools").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "services.mcp_client" not in source, path.name
        assert "call_tool(" not in source, path.name


def test_fastapi_does_not_start_mcp_client_as_a_runtime_dependency():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "start_mcp_client" not in source
    assert "stop_mcp_client" not in source
    assert "services.rag.startup" in source


async def test_fastmcp_exposes_required_search_capabilities():
    from mcp_server.server import mcp

    names = {tool.name for tool in await mcp.list_tools()}

    assert {"search_law", "search_case"}.issubset(names)
    assert {"search_local_law", "legal_search"}.issubset(names)


def test_agent_search_schemas_reuse_service_parameter_models():
    assert issubclass(LawSearchToolInput, LawSearchParams)
    assert issubclass(CaseSearchToolInput, CaseSearchParams)


def test_fastmcp_search_module_delegates_to_shared_services():
    source = (ROOT / "mcp_server" / "tools" / "search.py").read_text(encoding="utf-8")
    assert "search_law_service" in source
    assert "search_case_service" in source
    assert "DelilegalClient" not in source
    assert "get_retriever" not in source


def test_service_layer_does_not_import_fastmcp_runtime():
    for name in ("search.py", "legal_tools.py", "jurisdiction.py"):
        source = (ROOT / "services" / name).read_text(encoding="utf-8")
        assert "from mcp_server" not in source, name
        assert "import mcp_server" not in source, name
        assert "mcp.server" not in source, name


async def test_fastmcp_search_law_calls_shared_service(monkeypatch):
    from mcp_server.tools import search

    captured = {}

    async def fake_service(params, *, trace_id=None):
        captured["params"] = params
        captured["trace_id"] = trace_id
        return SearchServiceResult(
            status="found",
            source_type="delilegal_law",
            trace_id=trace_id or "generated",
            latency_ms=1,
            success=True,
            evidence_insufficient=False,
            result_count=1,
            results=[{"law_name": "民法典"}],
        )

    monkeypatch.setattr(search, "search_law_service", fake_service)

    payload = json.loads(await search.search_law("合同解除", top_k=3, trace_id="trace-mcp"))

    assert isinstance(captured["params"], LawSearchParams)
    assert captured["params"].top_k == 3
    assert captured["trace_id"] == "trace-mcp"
    assert payload["results"][0]["law_name"] == "民法典"
