"""Specialist Agent boundaries and report-contract regressions."""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage

from agent.agents.statute_retrieval_agent import statute_retrieval_agent_node
from agent.agents.fact_agent import case_analysis_agent_node
from agent.agents.legal_consult_agent import _legal_consult_tools_for_state
from agent.graph import build_graph
from agent.nodes.supervisor import _next_route_after_agent_reports


REQUIRED_REPORT_FIELDS = {
    "agent_name",
    "task_id",
    "summary",
    "findings",
    "sources",
    "confidence",
}


class _StatuteLLM:
    async def ainvoke(self, messages):
        return AIMessage(content=json.dumps({
            "agent_name": "statute_retrieval_agent",
            "summary": "已筛选劳动合同到期的法规依据",
            "findings": {
                "keywords": ["劳动合同到期", "经济补偿"],
                "relevance_assessment": [
                    {
                        "law_name": "劳动合同法",
                        "article_no": "第四十六条",
                        "relevant": True,
                        "reason": "规定经济补偿情形",
                    },
                    {
                        "law_name": "反间谍法",
                        "article_no": "第三十条",
                        "relevant": False,
                        "reason": "与劳动合同争议无关",
                    },
                ],
            },
            "confidence": "high",
        }, ensure_ascii=False))


class _CaseLLM:
    async def ainvoke(self, messages):
        return AIMessage(content=json.dumps({
            "summary": "已整理租赁押金争议",
            "status": "facts_sufficient",
            "findings": {
                "facts": ["租赁已到期", "房东未退押金"],
                "timeline": [],
                "parties": ["承租人", "出租人"],
                "legal_relationships": ["房屋租赁合同关系"],
                "disputed_issues": ["押金返还条件是否成就"],
                "claims_and_defenses": [],
                "evidence_gaps": ["退租交接记录"],
            },
            "confidence": "medium",
        }, ensure_ascii=False))


async def test_statute_agent_emits_grounded_structured_report(monkeypatch):
    monkeypatch.setattr("agent.nodes.get_llm", lambda **kwargs: _StatuteLLM())
    monkeypatch.setattr("agent.nodes.supports_tools", lambda provider=None: False)
    result = await statute_retrieval_agent_node({
        "messages": [HumanMessage(content="劳动合同到期不续签有什么法律依据？")],
        "trace_id": "task-42",
        "retrieved_laws": [
            {
                "law_name": "劳动合同法",
                "article_no": "第四十六条",
                "content": "用人单位应当支付经济补偿。",
                "source_type": "local_rag",
            },
            {
                "law_name": "反间谍法",
                "article_no": "第三十条",
                "content": "无关内容。",
                "source_type": "local_rag",
            },
        ],
    })

    report = result["agent_reports"][0]
    assert REQUIRED_REPORT_FIELDS <= report.keys()
    assert report["agent_name"] == "statute_retrieval_agent"
    assert report["task_id"] == "task-42"
    assert [item["law_name"] for item in report["findings"]["statutes"]] == ["劳动合同法"]
    assert [item["law_name"] for item in report["sources"]] == ["劳动合同法"]


async def test_case_analysis_agent_emits_common_report_envelope(monkeypatch):
    monkeypatch.setattr("agent.agents.fact_agent.should_ask_follow_up", lambda *args, **kwargs: {"should_ask": False})
    monkeypatch.setattr("agent.nodes.get_llm", lambda **kwargs: _CaseLLM())
    monkeypatch.setattr("agent.nodes.supports_tools", lambda provider=None: False)

    result = await case_analysis_agent_node({
        "messages": [HumanMessage(content="租赁到期后房东拒绝退还押金")],
        "trace_id": "task-case",
    })

    report = result["agent_reports"][0]
    assert REQUIRED_REPORT_FIELDS <= report.keys()
    assert report["agent_name"] == "case_analysis_agent"
    assert "disputed_issues" in report["findings"]


def test_legal_consult_does_not_repeat_completed_case_or_statute_work():
    tools = _legal_consult_tools_for_state({
        "agent_reports": [
            {"agent_name": "case_analysis_agent"},
            {"agent_name": "statute_retrieval_agent", "evidence_insufficient": False},
        ],
    })

    names = {tool.name for tool in tools}
    assert "search_case_tool" not in names
    assert "search_law_tool" not in names
    assert "retrieve_local_law_tool" not in names


def test_legal_consult_allows_law_search_when_statute_evidence_is_insufficient():
    tools = _legal_consult_tools_for_state({
        "agent_reports": [
            {
                "agent_name": "statute_retrieval_agent",
                "evidence_insufficient": True,
            },
        ],
    })

    names = {tool.name for tool in tools}
    assert "search_law_tool" in names


def test_supervisor_sequences_distinct_specialist_tasks_without_duplicates():
    case_report = {
        "agent_name": "case_analysis_agent",
        "status": "facts_sufficient",
    }
    statute_report = {
        "agent_name": "statute_retrieval_agent",
        "status": "report_ready",
    }

    route, _ = _next_route_after_agent_reports({"agent_reports": [case_report]})
    assert route == "statute_retrieval_agent"

    route, _ = _next_route_after_agent_reports({
        "agent_reports": [case_report, statute_report],
    })
    assert route == "legal_consult_agent"

    route, _ = _next_route_after_agent_reports({
        "agent_reports": [
            case_report,
            statute_report,
            {"agent_name": "legal_consult_agent", "status": "analysis_ready"},
        ],
    })
    assert route == "end"


def test_contract_agent_is_not_in_default_supervisor_graph():
    nodes = build_graph(checkpointer=None).get_graph().nodes

    assert "case_analysis_agent" in nodes
    assert "statute_retrieval_agent" in nodes
    assert "legal_consult_agent" in nodes
    assert "contract_agent" not in nodes
