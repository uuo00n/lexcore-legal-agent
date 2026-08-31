from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agent.graph import build_graph
from agent.nodes import case_analysis_agent_node, contract_agent_node, should_after_supervisor, supervisor_agent_node
from api.chat import ChatRequest, _build_state_input
from services.supervisor import SupervisorDecision


class FakeLLM:
    def __init__(self, content: str):
        self.content = content

    async def ainvoke(self, messages):
        return AIMessage(content=self.content)


async def test_supervisor_agent_node_sets_route(monkeypatch):
    async def fake_route(**kwargs):
        return SupervisorDecision(
            route="case_analysis_agent",
            reason="测试路由",
            complexity="low",
            need_tools=False,
        )

    monkeypatch.setattr("agent.nodes.route_user_request_with_llm", fake_route)
    state = {"messages": [HumanMessage(content="房东不退押金")]}

    result = await supervisor_agent_node(state)

    assert result["supervisor_route"] == "case_analysis_agent"
    assert should_after_supervisor(result) == "case_analysis_agent"


async def test_supervisor_agent_node_directly_finalizes(monkeypatch):
    async def fake_route(**kwargs):
        return SupervisorDecision(
            route="final",
            reason="非法律情绪表达，由主控直接回应",
            complexity="low",
            need_tools=False,
        )

    monkeypatch.setattr("agent.nodes.route_user_request_with_llm", fake_route)
    monkeypatch.setattr("agent.nodes.get_llm", lambda **kwargs: FakeLLM("我在，你慢慢说。"))

    result = await supervisor_agent_node({"messages": [HumanMessage(content="呜呜呜")]})

    assert result["supervisor_route"] == "end"
    assert result["supervisor_finalized"] is True
    assert result["messages"][0].content == "我在，你慢慢说。"
    assert should_after_supervisor(result) == "end"


async def test_case_analysis_agent_node_returns_follow_up_report(monkeypatch):
    monkeypatch.setattr("agent.nodes.get_llm", lambda **kwargs: FakeLLM("请补充租赁合同、退租原因和证据情况。"))

    result = await case_analysis_agent_node({"messages": [HumanMessage(content="房东不退押金")]})

    assert result["needs_follow_up"] is True
    assert "messages" not in result
    assert result["agent_reports"][0]["agent_name"] == "case_analysis_agent"
    assert result["agent_reports"][0]["status"] == "needs_more_facts"
    assert "请补充" in result["agent_reports"][0]["draft_response"]


async def test_contract_agent_node_asks_for_document_when_missing(monkeypatch):
    monkeypatch.setattr("agent.nodes.get_llm", lambda **kwargs: FakeLLM("请上传合同文件，我会生成审查报告。"))

    result = await contract_agent_node({"messages": [HumanMessage(content="帮我审查合同")]})

    assert "messages" not in result
    assert result["agent_reports"][0]["agent"] == "contract_agent"
    assert result["agent_reports"][0]["status"] == "missing_document"
    assert "上传合同" in result["agent_reports"][0]["draft_response"]


async def test_contract_agent_node_returns_structured_contract_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("agent.nodes.get_llm", lambda **kwargs: FakeLLM("发现单方解除和付款风险。"))

    result = await contract_agent_node({
        "messages": [HumanMessage(content="帮我审查这份服务合同")],
        "uploaded_doc_name": "服务合同.txt",
        "uploaded_doc_text": (
            "技术服务合同\n"
            "第一条 服务内容\n乙方提供技术服务，具体服务范围以甲方要求为准。\n"
            "第二条 付款\n甲方在认为合适时支付服务费。\n"
            "第三条 解除\n甲方可以随时解除合同，无需通知乙方。\n"
        ),
    })

    report = result["agent_reports"][0]
    assert report["agent"] == "contract_agent"
    assert report["status"] == "report_ready"
    assert report["contract_meta"]["contract_type"] == "service"
    assert report["overall_risk_level"] in {"medium", "high", "critical"}
    assert report["top_issues"]
    assert report["contract_result"]["issues"]
    assert report["report_id"].startswith("contract-")


async def test_supervisor_agent_node_finalizes_from_fact_report(monkeypatch):
    monkeypatch.setattr("agent.nodes.get_llm", lambda **kwargs: FakeLLM("主控整理后的追问。"))

    result = await supervisor_agent_node({
        "messages": [HumanMessage(content="房东不退押金")],
        "agent_reports": [
            {
                "agent": "fact_agent",
                "status": "needs_more_facts",
                "draft_response": "请补充租赁合同、退租原因和证据情况。",
            }
        ],
    })

    assert result["supervisor_route"] == "end"
    assert result["supervisor_finalized"] is True
    assert isinstance(result["messages"][0], AIMessage)
    assert result["messages"][0].content == "主控整理后的追问。"


async def test_graph_continues_through_specialists_when_case_facts_are_sufficient(monkeypatch):
    supervisor_calls = []

    async def fake_supervisor(state):
        supervisor_calls.append(state.get("agent_reports", []))
        if len(supervisor_calls) == 1:
            return {"supervisor_route": "case_analysis_agent", "supervisor_reason": "测试路由"}
        if len(supervisor_calls) == 2:
            return {"supervisor_route": "legal_consult_agent", "supervisor_reason": "事实已补足"}
        return {
            "supervisor_route": "end",
            "messages": [AIMessage(content="我会继续给出可执行建议。")],
        }

    async def fake_case_analysis(state):
        return {
            "needs_follow_up": False,
            "agent_reports": [
                {
                    "agent_name": "case_analysis_agent",
                    "agent": "case_analysis_agent",
                    "status": "facts_sufficient",
                    "summary": "事实足够进入法律分析",
                }
            ],
        }

    async def fake_legal_consult(state):
        return {
            "agent_reports": [
                {
                    "agent": "legal_consult_agent",
                    "status": "analysis_ready",
                    "analysis": "可以继续给出可执行建议。",
                }
            ],
        }

    monkeypatch.setattr("agent.graph.supervisor_agent_node", fake_supervisor)
    monkeypatch.setattr("agent.graph.case_analysis_agent_node", fake_case_analysis)
    monkeypatch.setattr("agent.graph.legal_consult_agent_node", fake_legal_consult)

    graph = build_graph(checkpointer=None)

    result = await graph.ainvoke({"messages": [HumanMessage(content="我被欺负了")]})

    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].content == "我会继续给出可执行建议。"


async def test_graph_returns_to_supervisor_after_case_analysis_report(monkeypatch):
    supervisor_calls = []

    async def fake_supervisor(state):
        supervisor_calls.append(state.get("agent_reports", []))
        if len(supervisor_calls) == 1:
            return {"supervisor_route": "case_analysis_agent", "supervisor_reason": "测试路由"}
        return {
            "supervisor_route": "end",
            "messages": [AIMessage(content="主控整理后的追问。")],
        }

    async def fake_case_analysis(state):
        return {
            "needs_follow_up": True,
            "agent_reports": [
                {
                    "agent_name": "case_analysis_agent",
                    "agent": "case_analysis_agent",
                    "status": "needs_more_facts",
                    "draft_response": "请补充租赁合同、退租原因和证据情况。",
                }
            ],
        }

    async def fake_legal_consult(state):
        raise AssertionError("事实不足时不应进入 legal_consult_agent")

    monkeypatch.setattr("agent.graph.supervisor_agent_node", fake_supervisor)
    monkeypatch.setattr("agent.graph.case_analysis_agent_node", fake_case_analysis)
    monkeypatch.setattr("agent.graph.legal_consult_agent_node", fake_legal_consult)

    graph = build_graph(checkpointer=None)

    result = await graph.ainvoke({"messages": [HumanMessage(content="房东不退押金")]})

    assert len(supervisor_calls) == 2
    assert supervisor_calls[1][0]["agent_name"] == "case_analysis_agent"
    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].content == "主控整理后的追问。"


async def test_graph_returns_to_supervisor_after_legal_consult_report(monkeypatch):
    supervisor_calls = []

    async def fake_supervisor(state):
        supervisor_calls.append(state.get("agent_reports", []))
        if len(supervisor_calls) == 1:
            return {"supervisor_route": "legal_consult_agent", "supervisor_reason": "测试路由"}
        return {
            "supervisor_route": "end",
            "messages": [AIMessage(content="主控整理后的法律答案。")],
        }

    async def fake_legal_consult(state):
        return {
            "agent_reports": [
                {
                    "agent": "legal_consult_agent",
                    "status": "analysis_ready",
                    "analysis": "可以主张经济补偿。",
                }
            ],
        }

    monkeypatch.setattr("agent.graph.supervisor_agent_node", fake_supervisor)
    monkeypatch.setattr("agent.graph.legal_consult_agent_node", fake_legal_consult)

    graph = build_graph(checkpointer=None)

    result = await graph.ainvoke({"messages": [HumanMessage(content="劳动合同到期不续签")]})

    assert len(supervisor_calls) == 2
    assert supervisor_calls[1][0]["agent"] == "legal_consult_agent"
    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].content == "主控整理后的法律答案。"


def test_multi_agent_graph_compiles():
    graph = build_graph(checkpointer=None)

    assert graph is not None


async def test_chat_input_resets_stale_checkpoint_control_state(monkeypatch):
    thread_id = "test-stale-control-state"
    legal_queries = []

    async def fake_route(**kwargs):
        return SupervisorDecision(
            route="legal_consult_agent",
            reason="测试路由到法律咨询",
            complexity="medium",
            need_tools=True,
        )

    async def fake_memory(state):
        return {}

    async def fake_legal_consult(state):
        latest = [m.content for m in state.get("messages", []) if isinstance(m, HumanMessage)][-1]
        legal_queries.append(latest)
        return {
            "agent_reports": [
                {
                    "agent": "legal_consult_agent",
                    "status": "analysis_ready",
                    "analysis": f"法律咨询已处理：{latest}",
                }
            ]
        }

    monkeypatch.setattr("agent.nodes.route_user_request_with_llm", fake_route)
    monkeypatch.setattr("agent.nodes.get_llm", lambda **kwargs: FakeLLM("主控整理后的法律答案。"))
    monkeypatch.setattr("agent.graph.memory_node", fake_memory)
    monkeypatch.setattr("agent.graph.legal_consult_agent_node", fake_legal_consult)

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": thread_id}}

    await graph.ainvoke(
        {
            "messages": [HumanMessage(content="劳动合同到期不续签有补偿吗？")],
            "thread_id": thread_id,
            "trace_id": "",
        },
        config,
    )
    assert legal_queries == ["劳动合同到期不续签有补偿吗？"]

    req = ChatRequest(
        thread_id=thread_id,
        message="劳动合同到期公司不续签，我工作三年，可以要求哪些补偿？",
    )
    state_input = _build_state_input(
        graph,
        req,
        doc_text=None,
        doc_name=None,
        trace_id="",
    )
    result = await graph.ainvoke(state_input, config)

    assert legal_queries == [
        "劳动合同到期不续签有补偿吗？",
        "劳动合同到期公司不续签，我工作三年，可以要求哪些补偿？",
    ]
    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].content == "主控整理后的法律答案。"
