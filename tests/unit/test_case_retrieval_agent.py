"""Case Retrieval Agent 的职责边界与证据接地（§五、§P0-5）。"""
from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.agent_names import CASE_RETRIEVAL_AGENT, agent_node
from agent.agents.case_retrieval_agent import case_retrieval_agent_node
from agent.repair import REPAIR_ROUTING_MAP, resolve_repair_node
from agent.tools import CASE_RETRIEVAL_TOOLS


RETRIEVED = [
    {
        "case_no": "（2021）京01民终1234号",
        "case_name": "张某与某公司劳动争议案",
        "dispute_focus": "违法解除劳动合同的赔偿标准",
        "source_type": "case_api",
    },
    {
        "case_no": "（2020）沪02民终5678号",
        "case_name": "李某与某物业公司劳动争议案",
        "dispute_focus": "未签书面合同的二倍工资",
        "source_type": "case_api",
    },
]


class _CaseLLM:
    """按 CASE_RETRIEVAL_SYSTEM_PROMPT 约定返回结构化 JSON 的假模型。"""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def ainvoke(self, messages):
        return AIMessage(content=json.dumps(self._payload, ensure_ascii=False))


def _state(**overrides):
    state = {
        "messages": [HumanMessage(content="公司违法解除劳动合同，类似案例法院怎么判？")],
        "trace_id": "task-case-retrieval",
        "retrieved_cases": [dict(item) for item in RETRIEVED],
    }
    state.update(overrides)
    return state


@pytest.fixture(autouse=True)
def _no_tool_binding(monkeypatch):
    monkeypatch.setattr("agent.nodes.supports_tools", lambda provider=None: False)


async def test_case_retrieval_agent_reports_only_cases_retrieved_this_round(monkeypatch):
    """模型自行写出的案号必须被丢弃，只有本轮检索结果能进报告（§P0-1）。"""
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: _CaseLLM({
        "agent_name": "case_retrieval_agent",
        "summary": "筛选出与违法解除争议焦点一致的案例",
        "findings": {
            "keywords": ["违法解除", "赔偿金"],
            "cases": [
                {"case_no": "（2021）京01民终1234号", "case_name": "张某与某公司劳动争议案"},
                # 记忆里编出来的案号：本轮 retrieved_cases 里没有，必须被过滤掉。
                {"case_no": "（2019）粤03民终9999号", "case_name": "凭记忆写出的案例"},
            ],
            "relevance_assessment": [
                {
                    "case_no": "（2021）京01民终1234号",
                    "case_name": "张某与某公司劳动争议案",
                    "relevant": True,
                    "reason": "同为违法解除赔偿争议",
                },
            ],
            "evidence_insufficient": False,
        },
        "confidence": "high",
    }))

    result = await case_retrieval_agent_node(_state())

    report = result["agent_reports"][0]
    assert report["agent_name"] == CASE_RETRIEVAL_AGENT
    assert report["task_id"] == "task-case-retrieval"
    assert [item["case_no"] for item in report["cases"]] == ["（2021）京01民终1234号"]
    assert [item["case_no"] for item in report["sources"]] == ["（2021）京01民终1234号"]
    assert report["evidence_insufficient"] is False
    # 只提交案例证据：不整理事实、不检索法规、不给法律结论（§五）。
    assert set(report["findings"]) == {
        "query",
        "keywords",
        "cases",
        "relevance_assessment",
        "evidence_insufficient",
    }


async def test_case_retrieval_agent_flags_insufficient_evidence_for_the_repair_router(monkeypatch):
    """检索不到案例时必须显式报告证据不足，让核验与修复能接手（§P0-5）。"""
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: _CaseLLM({
        "agent_name": "case_retrieval_agent",
        "summary": "未检索到相近案例",
        "findings": {"keywords": ["违法解除"], "cases": [], "relevance_assessment": []},
        "confidence": "low",
    }))

    result = await case_retrieval_agent_node(_state(retrieved_cases=[]))

    report = result["agent_reports"][0]
    assert report["cases"] == []
    assert report["evidence_insufficient"] is True
    assert report["confidence"] == "low"


async def test_case_retrieval_agent_drops_cases_the_model_marks_irrelevant(monkeypatch):
    """模型判定不相关的案例不进报告；相关性判断可以交给模型，接地由代码保证。"""
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: _CaseLLM({
        "agent_name": "case_retrieval_agent",
        "summary": "剔除争议焦点不一致的案例",
        "findings": {
            "keywords": ["违法解除"],
            "relevance_assessment": [
                {
                    "case_no": "（2020）沪02民终5678号",
                    "case_name": "李某与某物业公司劳动争议案",
                    "relevant": False,
                    "reason": "争议焦点是二倍工资，与违法解除无关",
                },
            ],
        },
    }))

    result = await case_retrieval_agent_node(_state())

    report = result["agent_reports"][0]
    assert [item["case_no"] for item in report["cases"]] == ["（2021）京01民终1234号"]


async def test_case_retrieval_agent_falls_back_to_keywords_when_the_model_returns_prose(monkeypatch):
    """模型没给 JSON 时仍要产出可用报告，而不是让整轮计划失败。"""
    class _ProseLLM:
        async def ainvoke(self, messages):
            return AIMessage(content="我找到了一些相近的劳动争议判决。")

    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: _ProseLLM())

    result = await case_retrieval_agent_node(_state())

    report = result["agent_reports"][0]
    assert report["keywords"]
    assert len(report["cases"]) == 2
    assert report["evidence_insufficient"] is False


def test_case_retrieval_agent_is_bound_to_case_tools_only():
    """§五 的职责边界由工具集合保证：类案 Agent 拿不到法规检索工具。"""
    names = {tool.name for tool in CASE_RETRIEVAL_TOOLS}

    assert names == {"search_case_tool"}


def test_case_evidence_repair_routes_to_the_dedicated_case_node():
    """§P0-5：类案证据不足只重跑类案检索，不再回到事实分析节点。"""
    target = REPAIR_ROUTING_MAP["case_evidence_insufficient"]

    assert target == CASE_RETRIEVAL_AGENT
    assert resolve_repair_node(target) == "case_retrieval_agent"
    assert agent_node(CASE_RETRIEVAL_AGENT) == "case_retrieval_agent"
