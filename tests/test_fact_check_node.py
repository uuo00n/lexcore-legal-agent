from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from agent.nodes import fact_check_node, should_after_fact_check


class FakeLLM:
    async def ainvoke(self, messages):
        return AIMessage(content="请补充租赁合同、退租原因和证据情况。")


async def test_fact_check_node_returns_follow_up_for_sparse_legal_question(monkeypatch):
    monkeypatch.setattr("agent.nodes.get_llm", lambda **kwargs: FakeLLM())

    result = await fact_check_node({"messages": [HumanMessage(content="房东不退押金")]})

    assert result["needs_follow_up"] is True
    assert "messages" not in result
    report = result["agent_reports"][0]
    assert report["agent_name"] == "case_analysis_agent"
    assert {"agent_name", "task_id", "summary", "findings", "sources", "confidence"} <= report.keys()
    assert "请补充" in result["agent_reports"][0]["draft_response"]


async def test_fact_check_node_allows_non_legal_question():
    result = await fact_check_node({"messages": [HumanMessage(content="你好")]})

    assert result["needs_follow_up"] is False
    assert should_after_fact_check(result) == "agent"
