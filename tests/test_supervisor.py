from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from services.supervisor import route_user_request, route_user_request_with_llm


class _MisroutingLLM:
    async def ainvoke(self, messages):
        return AIMessage(content='{"route":"case_analysis_agent","reason":"需要案件分析","complexity":"low","need_tools":false}')


class _ConsultLLM:
    async def ainvoke(self, messages):
        return AIMessage(content='{"route":"legal_consult_agent","reason":"直接咨询","complexity":"medium","need_tools":true}')


class _FailingLLM:
    async def ainvoke(self, messages):
        raise RuntimeError("llm unavailable")


def test_supervisor_routes_sparse_legal_question_to_case_analysis_agent():
    decision = route_user_request(message="房东不退押金")

    assert decision.route == "case_analysis_agent"
    assert decision.need_tools is False


def test_supervisor_removes_contract_agent_from_default_route():
    decision = route_user_request(
        message="帮我看看这份合同有没有坑",
        has_uploaded_doc=True,
        uploaded_doc_name="服务合同.docx",
    )

    assert decision.route == "case_analysis_agent"
    assert decision.complexity == "medium"


def test_supervisor_routes_normal_legal_question_to_consult_agent():
    decision = route_user_request(message="劳动仲裁应该去哪里申请？")

    assert decision.route == "legal_consult_agent"
    assert decision.need_tools is True


def test_supervisor_directly_answers_non_legal_chitchat():
    decision = route_user_request(message="呜呜呜")

    assert decision.route == "final"
    assert decision.need_tools is False


def test_supervisor_routes_drug_plant_threshold_question_to_statute_agent():
    decision = route_user_request(message="种植罂粟几株犯法")

    assert decision.route == "statute_retrieval_agent"
    assert decision.need_tools is True


@pytest.mark.asyncio
async def test_llm_supervisor_is_primary_when_available(monkeypatch):
    monkeypatch.setattr("services.supervisor.get_llm", lambda **kwargs: _MisroutingLLM())

    decision = await route_user_request_with_llm(message="种植罂粟几株犯法")

    assert decision.route == "case_analysis_agent"
    assert decision.need_tools is False


@pytest.mark.asyncio
async def test_llm_supervisor_can_route_sparse_question_to_consult_agent(monkeypatch):
    monkeypatch.setattr("services.supervisor.get_llm", lambda **kwargs: _ConsultLLM())

    decision = await route_user_request_with_llm(message="房东不退押金")

    assert decision.route == "legal_consult_agent"
    assert decision.need_tools is True


@pytest.mark.asyncio
async def test_rules_are_only_fallback_when_llm_supervisor_fails(monkeypatch):
    monkeypatch.setattr("services.supervisor.get_llm", lambda **kwargs: _FailingLLM())

    decision = await route_user_request_with_llm(message="房东不退押金")

    assert decision.route == "case_analysis_agent"
    assert "规则兜底" in decision.reason
