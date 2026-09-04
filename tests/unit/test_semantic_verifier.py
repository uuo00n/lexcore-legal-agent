"""Semantic Verifier 的结构化问题与降级行为测试（§十四、§P1-6、§三十 用例 8）。

确定性 Citation Verifier 与语义 Semantic Verifier 是两个半边：前者决定引用是否成立，
后者只补充难以规则化的判断。这里覆盖三件事——
1. 语义半边报错时只降级（``verification_degraded=true``），确定性结论照旧生效、不抛异常；
2. 语义半边给出的 ``{type, step_id, agent, message}`` 能落到 Repair Router 的修复目标；
3. 模型编造的问题类型与归属会被丢弃或清空，不得借语义半边推翻引用核验、误导局部修复。
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage

from agent.nodes.routing import should_after_verifier
from agent.nodes.verifier import result_verifier_node
from agent.state import TaskType


class _BrokenVerifierLLM:
    """模拟 Semantic Verifier 的 Provider 故障（§三十 用例 8）。"""

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        raise RuntimeError("verifier provider unavailable: 503")


class _IssueVerifierLLM:
    """按 §十四 返回结构化 issues 的语义核验替身。"""

    def __init__(self, issues: list[dict]) -> None:
        self._issues = issues

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        return {
            "issues": self._issues,
            "severe_conflicts": [],
            "unsupported_conclusions": [],
            "obsolete_law_risks": [],
            "missing_sources": [],
        }


def _grounded_state(law: dict) -> dict:
    """引用全部落在检索证据上的状态：确定性核验必然通过。"""
    report = {
        "report_id": "step_1:legal_consult_agent",
        "task_id": "step_1",
        "agent_name": "legal_consult_agent",
        "summary": "可以要求经济补偿",
        "findings": {
            "analysis": f"根据《{law['law_name']}》{law['article_no']}，用人单位应当支付经济补偿。"
        },
        "sources": [law],
    }
    return {
        "messages": [HumanMessage(content="公司无故解除劳动合同怎么办？")],
        "plan": [{
            "step_id": "step_1",
            "task_type": TaskType.LEGAL_CONSULTATION,
            "description": "形成法律建议",
            "assigned_agent": "legal_consult_agent",
            "required": True,
            "status": "completed",
            "result": report,
        }],
        "agent_reports": [report],
        "retrieved_laws": [law],
        "retrieved_cases": [],
        "replan_retry_count": 0,
    }


def _hallucinated_state(law: dict) -> dict:
    """引用了检索结果里没有的条文：确定性核验必然失败。"""
    state = _grounded_state(law)
    report = state["agent_reports"][0]
    report["findings"] = {
        "analysis": f"根据《{law['law_name']}》第九十九条，公司必须支付双倍赔偿。"
    }
    report["sources"] = [{**law, "article_no": "第九十九条"}]
    state["plan"][0]["result"] = report
    return state


async def test_semantic_verifier_failure_only_degrades(monkeypatch, grounded_law):
    """§P1-6、§三十 用例 8：语义半边报错不抛 500，确定性问题照旧产出。"""
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: _BrokenVerifierLLM())

    result = await result_verifier_node(_hallucinated_state(grounded_law))

    verification = result["verification_result"]
    assert verification["verification_degraded"] is True
    # 确定性核验不受语义半边影响：伪造引用照样拦下并落到局部修复。
    assert verification["passed"] is False
    assert verification["invalid_citations"]
    assert verification["repair_targets"] == ["law_retrieval_agent", "legal_reasoning_agent"]
    assert result["supervisor_route"] == "repair"
    assert should_after_verifier(result) == "repair"
    # 降级只是缺少语义补充，不能凭空造出语义来源的问题。
    assert all(
        issue.get("source") != "semantic"
        for issue in verification["structured_issues"]
    )


async def test_semantic_verifier_success_is_not_degraded(monkeypatch, grounded_law):
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: _IssueVerifierLLM([]))

    result = await result_verifier_node(_grounded_state(grounded_law))

    verification = result["verification_result"]
    assert verification["verification_degraded"] is False
    assert verification["passed"] is True
    assert verification["structured_issues"] == []
    assert result["supervisor_route"] == "answer_generator"


async def test_semantic_issue_routes_to_local_repair(monkeypatch, grounded_law):
    """§十四 + §P0-5：语义问题带着 step_id / agent 回来，直接落到对应执行单元。"""
    monkeypatch.setattr(
        "agent.nodes.get_llm",
        lambda **_kwargs: _IssueVerifierLLM([{
            "type": "overconfident",
            "step_id": "step_1",
            "agent": "legal_consult_agent",
            "message": "在没有解除通知的情况下断定必然获得双倍赔偿",
        }]),
    )

    result = await result_verifier_node(_grounded_state(grounded_law))

    verification = result["verification_result"]
    assert verification["verification_degraded"] is False
    assert verification["passed"] is False
    issue = next(
        item for item in verification["structured_issues"] if item["source"] == "semantic"
    )
    assert issue["type"] == "overconfident"
    assert issue["step_id"] == "step_1"
    assert issue["agent"] == "legal_consult_agent"
    assert "结论明显缺少依据" in issue["message"]
    assert verification["repair_targets"] == ["legal_reasoning_agent"]
    # 语义半边没有质疑引用，所以引用核验仍然全部通过（P0-1 单一事实源）。
    assert verification["invalid_citations"] == []
    assert should_after_verifier(result) == "repair"


async def test_semantic_verifier_cannot_rejudge_citations(monkeypatch, grounded_law):
    """§一：不允许 LLM 重新判断引用；白名单外的问题类型一律丢弃。"""
    monkeypatch.setattr(
        "agent.nodes.get_llm",
        lambda **_kwargs: _IssueVerifierLLM([
            {
                "type": "citation_invalid",
                "step_id": "step_1",
                "agent": "legal_consult_agent",
                "message": "这条法条我认为不成立",
            },
            {
                "type": "plan_incomplete",
                "step_id": "step_1",
                "agent": "legal_consult_agent",
                "message": "计划缺少类案检索步骤",
            },
        ]),
    )

    result = await result_verifier_node(_grounded_state(grounded_law))

    verification = result["verification_result"]
    assert verification["passed"] is True
    assert verification["structured_issues"] == []
    assert verification["invalid_citations"] == []
    assert result["supervisor_route"] == "answer_generator"


async def test_semantic_issue_with_unknown_target_is_blanked(monkeypatch, grounded_law):
    """模型编造的 step_id / agent 不得进入修复路由，否则会重跑错误的执行单元。"""
    monkeypatch.setattr(
        "agent.nodes.get_llm",
        lambda **_kwargs: _IssueVerifierLLM([{
            "type": "reasoning_conflict",
            "step_id": "step_9",
            "agent": "tax_agent",
            "message": "两份报告对补偿基数的认定互相矛盾",
        }]),
    )

    result = await result_verifier_node(_grounded_state(grounded_law))

    verification = result["verification_result"]
    issue = next(
        item for item in verification["structured_issues"] if item["source"] == "semantic"
    )
    assert issue["type"] == "reasoning_conflict"
    # 问题本身保留，但归属清空：由 Repair Router 按类型兜底，而不是按幻觉目标。
    assert issue.get("step_id", "") == ""
    assert issue.get("agent", "") == ""
    assert verification["repair_targets"] == ["legal_reasoning_agent"]


async def test_semantic_issue_with_ungrounded_citation_is_dropped(monkeypatch, grounded_law):
    """语义半边也不能引入输入材料里不存在的法条或案号。"""
    monkeypatch.setattr(
        "agent.nodes.get_llm",
        lambda **_kwargs: _IssueVerifierLLM([{
            "type": "obsolete_law_risk",
            "step_id": "step_1",
            "agent": "legal_consult_agent",
            "message": "《中华人民共和国劳动法》第九十一条可能已被修订",
        }]),
    )

    result = await result_verifier_node(_grounded_state(grounded_law))

    verification = result["verification_result"]
    assert verification["passed"] is True
    assert verification["structured_issues"] == []
