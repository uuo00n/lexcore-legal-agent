"""最终结果一致性测试（§P2、§三十 用例 10、§二 问题 12）。

同一份答复曾经在三处各自评分一次：Answer Generator 的 trace、Intent Router 的直答分支、
以及 ``api/chat.py`` 顶层的 ``analyze_legal_message``。评分口径一旦漂移，用户看到的
``legal_analysis.answer_score`` 就和 Verifier 的结论对不上。

这里锁死单一产出口：``answer_score`` 只由生成答复的节点算一次并写进 State，
``analyze_legal_message`` 直接复用；``answer_score.citations``、``legal_analysis.citations``
与 ``verification_result`` / ``verified_evidence`` 必须描述同一批引用。
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from agent.nodes.answer import answer_generator_node
from agent.nodes.verifier import result_verifier_node
from agent.state import TaskType
from services.legal_analysis import analyze_legal_message


class _NoIssueSemanticLLM:
    """语义核验半边：不报任何问题，避免降级掩盖本用例要验证的一致性。"""

    async def ainvoke(self, _messages):
        return {
            "issues": [],
            "severe_conflicts": [],
            "unsupported_conclusions": [],
            "obsolete_law_risks": [],
            "missing_sources": [],
        }


class _GroundedAnswerLLM:
    """只引用输入证据的答复模型替身；结构化输出分支交给语义核验用。"""

    def with_structured_output(self, _schema):
        return _NoIssueSemanticLLM()

    async def ainvoke(self, _messages):
        return AIMessage(content=(
            "1. 结论\n在现有材料下可以要求用人单位支付经济补偿。\n"
            "2. 法律分析\n根据《劳动合同法》第四十六条，符合法定情形的应当支付经济补偿。\n"
            "3. 法律依据\n《劳动合同法》第四十六条。\n"
            "4. 类案参考\n本轮没有可供引用的检索案例。\n"
            "5. 风险与不确定性\n仍需结合完整证据判断。\n"
            "6. 建议下一步\n先固定工资与考勤凭证。"
        ))


def _state(law: dict) -> dict:
    report = {
        "report_id": "step_1:legal_consult_agent",
        "task_id": "step_1",
        "agent_name": "legal_consult_agent",
        "summary": "可以要求支付经济补偿",
        "findings": {
            "analysis": f"根据《{law['law_name']}》{law['article_no']}，用人单位应当支付经济补偿。"
        },
        "sources": [law],
    }
    return {
        "messages": [HumanMessage(content="公司拖欠工资，我可以要求什么？")],
        "original_query": "公司拖欠工资，我可以要求什么？",
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


async def test_answer_score_is_produced_once_and_reused_by_api_layer(monkeypatch, grounded_law):
    """§三十 用例 10：answer_score、legal_analysis 与核验结果描述同一批引用。"""
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: _GroundedAnswerLLM())
    state = _state(grounded_law)

    verified = await result_verifier_node(state)
    state.update(verified)
    answer = await answer_generator_node(state)
    content = answer["messages"][0].content

    answer_score = answer["answer_score"]
    verification = verified["verification_result"]
    verified_laws = verified["verified_evidence"]["laws"]

    # 答复里的引用全部落在已核验法条上：三处结论必须一致（P0-1）。
    assert verification["verification_degraded"] is False
    assert verification["passed"] is True
    assert verification["invalid_citations"] == []
    assert answer_score["citations"]["is_fully_supported"] is True
    assert answer_score["citations"]["unsupported"] == []
    assert answer_score["citations"]["total"] > 0
    assert len(answer_score["citations"]["verified"]) == answer_score["citations"]["total"]
    assert verification["citation_report"]["citation_unsupported"] == 0

    # API 层不再重算，直接复用节点算好的那一份（§二 问题 12）。
    analysis = analyze_legal_message(
        state["original_query"],
        content,
        verified_laws,
        answer_score=answer_score,
    )
    assert analysis["answer_score"] == answer_score
    assert analysis["citations"] == answer_score["citations"]
    assert [item["source_id"] for item in answer["citations"]] == [grounded_law["source_id"]]


async def test_analyze_legal_message_still_scores_when_node_score_is_missing(grounded_law):
    """缓存命中等拿不到节点评分的旧链路仍要有评分，口径与节点一致。"""
    answer = f"根据《{grounded_law['law_name']}》{grounded_law['article_no']}，可以要求支付经济补偿。"

    reused = analyze_legal_message("公司拖欠工资", answer, [grounded_law], answer_score=None)
    recomputed = analyze_legal_message("公司拖欠工资", answer, [grounded_law])

    assert reused["answer_score"] == recomputed["answer_score"]
    assert reused["answer_score"]["citations"]["is_fully_supported"] is True
