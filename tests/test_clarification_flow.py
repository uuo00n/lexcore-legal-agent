"""澄清补问的端到端回归（§三十 用例 2、用例 3、§七、§八）。

在真实编译图上跑两轮同一个 ``thread_id``：

- 第一轮：``公司辞退我，我能赔多少钱？`` 事实不足 → 停在 Clarification 节点，
  不进 Planner、不进任何检索/咨询专家，也不给出任何金额；
- 第二轮：用户只回一句简短补充 → Fact Merge 把它并回原始问题 →
  Fact Analysis 重新判定事实已足 → Planner → Supervisor → 专家 → 最终答案，
  绝不允许直接跳过 Fact Analysis 冲进 Planner（§八）。

``memory`` 与最末端的咨询专家用假实现替换，避免依赖数据库与向量库；
Fact Analysis、Fact Merge、Intent Router、Clarification、Planner、Supervisor、
Verifier、Answer Generator 全部跑真实实现，这样路由本身才是被测对象。
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agent.graph import build_graph
from agent.nodes.clarification import ORIGINAL_QUESTION_KEY
from api.chat import ChatRequest, _build_state_input
from services.supervisor import SupervisorDecision

INDIVIDUAL_CONCLUSION_QUESTION = "公司辞退我，我能赔多少钱？"
USER_SUPPLEMENT = "我干了3年，月薪8000元，上个月被通知辞退，有劳动合同和工资流水"

_FACT_PAYLOAD = {
    "legal_relationship": "劳动合同关系",
    "facts": ["用户被公司辞退"],
    "legal_issues": ["辞退是否合法", "赔偿标准如何计算"],
    "missing_facts": ["工作年限", "月工资"],
    "facts_sufficient": False,
    "needs_clarification": True,
    "clarification_questions": [
        "你在这家公司工作了多久？",
        "你的月工资是多少（税前或到手）？",
    ],
}

_PLAN_PAYLOAD = {
    "steps": [
        {
            "step_id": "step_1",
            "task_type": "legal_consultation",
            "description": "综合已确认事实与法律依据给出可执行建议",
            "assigned_agent": "legal_consult_agent",
        }
    ]
}


class _AllPurposeLLM:
    """覆盖本轮全部模型调用的假模型：自由文本 + 各节点的结构化输出。

    ``schemas`` 记录被请求过的结构化 schema 名，用来断言某个节点到底跑没跑。
    未登记的 schema 直接抛错，让对应节点走自己的降级分支（§P1-5、§P1-6）。
    """

    def __init__(self, content: str = "综合以上材料形成的最终答复。"):
        self.content = content
        self.schemas: list[str] = []

    async def ainvoke(self, messages):
        return AIMessage(content=self.content)

    def with_structured_output(self, schema):
        name = schema.__name__
        self.schemas.append(name)
        payloads = {"FactAnalysisOutput": _FACT_PAYLOAD, "PlannerOutput": _PLAN_PAYLOAD}

        class _Structured:
            async def ainvoke(self, messages):
                if name not in payloads:
                    raise RuntimeError(f"no stub for {name}")
                return schema.model_validate(payloads[name])

        return _Structured()


async def test_clarification_blocks_the_first_turn_then_resumes_on_the_same_thread(monkeypatch):
    thread_id = "test-clarification-resume"
    llm = _AllPurposeLLM()
    consulted_queries: list[str] = []

    async def fake_route(**kwargs):
        return SupervisorDecision(
            route="legal_consult_agent",
            reason="劳动争议咨询",
            complexity="medium",
            need_tools=False,
        )

    async def fake_memory(state):
        return {}

    async def fake_legal_consult(state):
        consulted_queries.append(str(state.get("rewritten_query") or ""))
        return {
            "agent_reports": [
                {
                    "agent": "legal_consult_agent",
                    "status": "analysis_ready",
                    "analysis": "已按已确认事实给出赔偿计算口径。",
                }
            ]
        }

    monkeypatch.setattr("agent.nodes.route_user_request_with_llm", fake_route)
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: llm)
    monkeypatch.setattr("agent.graph.memory_node", fake_memory)
    monkeypatch.setattr("agent.graph.legal_consult_agent_node", fake_legal_consult)

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": thread_id}}

    # ── 第一轮：必须先补问（§三十 用例 2）────────────────────────────────
    first = await graph.ainvoke(
        {
            "messages": [HumanMessage(content=INDIVIDUAL_CONCLUSION_QUESTION)],
            "thread_id": thread_id,
            "trace_id": "",
        },
        config,
    )

    assert first["needs_clarification"] is True
    assert first["clarification_blocking"] is True
    assert first["clarification_round"] == 1
    assert first["confirmed_facts"] == {ORIGINAL_QUESTION_KEY: INDIVIDUAL_CONCLUSION_QUESTION}
    # 补问阻断时 Planner 与专家都不该被启动（§二 问题 2、§九）。
    assert "PlannerOutput" not in llm.schemas
    assert consulted_queries == []
    assert not first.get("plan")
    question_message = first["messages"][-1]
    assert isinstance(question_message, AIMessage)
    assert "你在这家公司工作了多久？" in question_message.content
    # 事实不足时不得出现任何确定金额（§三十 用例 2）。
    assert "元" not in question_message.content

    # ── 第二轮：同一 thread 补充事实后必须继续走完（§三十 用例 3）───────────
    state_input = _build_state_input(
        graph,
        ChatRequest(thread_id=thread_id, message=USER_SUPPLEMENT),
        doc_text=None,
        doc_name=None,
        trace_id="",
    )
    second = await graph.ainvoke(state_input, config)

    assert second["clarification_resumed"] is True
    # Fact Merge 把原始问题和补充拼回一句完整的问题，Fact Analysis 才判得出事实已足。
    assert second["rewritten_query"] == f"{INDIVIDUAL_CONCLUSION_QUESTION} {USER_SUPPLEMENT}"
    assert second["facts_sufficient"] is True
    assert second["needs_clarification"] is False
    assert second["clarification_blocking"] is False
    # 补问不再重复：轮次没有继续增长，Clarification 节点这一轮没跑。
    assert second["clarification_round"] == 1
    assert second["confirmed_facts"][ORIGINAL_QUESTION_KEY] == INDIVIDUAL_CONCLUSION_QUESTION
    assert USER_SUPPLEMENT in second["confirmed_facts"].values()
    # Fact Analysis → Planner → Supervisor → 专家，一步都不能少（§八）。
    assert "PlannerOutput" in llm.schemas
    assert second["plan"] and second["plan"][0]["assigned_agent"] == "legal_consult_agent"
    # 专家每次都只看到合并后的问题；调用次数由核验/修复预算决定，不在本用例的口径内。
    assert consulted_queries
    assert set(consulted_queries) == {f"{INDIVIDUAL_CONCLUSION_QUESTION} {USER_SUPPLEMENT}"}
    assert isinstance(second["messages"][-1], AIMessage)
    assert second["messages"][-1].content
