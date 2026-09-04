"""简单路径的端到端回归（§三十 用例 1、§九、§P1-1、§五）。

在真实编译图上跑一轮 ``公司拖欠我三个月工资怎么办``：单一法律关系、单一争议焦点，
Complexity Router 应该直接给出固定的最小计划（法规检索 → 法律推理）并交给
Supervisor 顺序执行，全程不进 Planner、不查类案、不做案件结构化分析。

``memory`` 和三个专家节点用假实现替换，避免依赖数据库、向量库与真实模型；
Intent Router、Fact Analysis、Complexity Router、Supervisor、Verifier、
Answer Generator 全部跑真实实现，这样路由本身才是被测对象。
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from agent.graph import build_graph
from agent.state import TaskType
from services.supervisor import SupervisorDecision

SIMPLE_WAGE_QUESTION = "公司拖欠我三个月工资怎么办"


class _AllPurposeLLM:
    """自由文本一律返回同一句；结构化输出全部不提供，用来断言哪个节点没跑。"""

    def __init__(self, content: str = "按现有材料整理的答复。"):
        self.content = content
        self.schemas: list[str] = []

    async def ainvoke(self, messages):
        return AIMessage(content=self.content)

    def with_structured_output(self, schema):
        name = schema.__name__
        self.schemas.append(name)

        class _Structured:
            async def ainvoke(self, messages):
                # 没有登记 stub：对应节点必须自己降级，不许 500（§P1-5、§P1-6）。
                raise RuntimeError(f"no stub for {name}")

        return _Structured()


async def test_simple_wage_question_runs_the_fixed_minimal_plan(monkeypatch):
    llm = _AllPurposeLLM()
    dispatched: list[str] = []

    async def fake_route(**kwargs):
        return SupervisorDecision(
            route="statute_retrieval_agent",
            reason="劳动欠薪法条咨询",
            complexity="low",
            need_tools=True,
        )

    async def fake_memory(state):
        return {}

    async def fake_statute_retrieval(state):
        dispatched.append("statute_retrieval_agent")
        return {
            "agent_reports": [
                {
                    "agent_name": "statute_retrieval_agent",
                    "status": "analysis_ready",
                    "summary": "已检索欠薪相关的现行规定。",
                }
            ]
        }

    async def fake_legal_consult(state):
        dispatched.append("legal_consult_agent")
        return {
            "agent_reports": [
                {
                    "agent_name": "legal_consult_agent",
                    "status": "analysis_ready",
                    "analysis": "可以先要求补发，协商不成再申请劳动仲裁。",
                }
            ]
        }

    async def fail_case_analysis(state):
        raise AssertionError("简单法条咨询不应进入案件分析或类案检索（§五）")

    monkeypatch.setattr("agent.nodes.route_user_request_with_llm", fake_route)
    monkeypatch.setattr("agent.nodes.get_llm", lambda **_kwargs: llm)
    monkeypatch.setattr("agent.graph.memory_node", fake_memory)
    monkeypatch.setattr("agent.graph.statute_retrieval_agent_node", fake_statute_retrieval)
    monkeypatch.setattr("agent.graph.legal_consult_agent_node", fake_legal_consult)
    monkeypatch.setattr("agent.graph.case_analysis_agent_node", fail_case_analysis)

    graph = build_graph(checkpointer=None)
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content=SIMPLE_WAGE_QUESTION)],
            "thread_id": "test-simple-route",
            "trace_id": "",
        }
    )

    # ── 定档与执行路径（§九、§P1-1）──────────────────────────────────────
    assert result["complexity_level"] == "simple"
    assert result["execution_mode"] == "simple"
    assert result["task_complexity"] == "low"
    # 简单问题不追问，也不阻断（§三十 用例 1 与用例 2 的分界）。
    assert result["facts_sufficient"] is True
    assert result["needs_clarification"] is False

    # ── 固定最小计划，Planner 完全没被启动 ───────────────────────────────
    assert [step["task_type"] for step in result["plan"]] == [
        TaskType.STATUTE_RETRIEVAL,
        TaskType.LEGAL_CONSULTATION,
    ]
    assert "PlannerOutput" not in llm.schemas
    # Fact Analysis 走确定性闸门，这一轮也不该多付一次模型调用（§二十六 延迟目标）。
    assert "FactAnalysisOutput" not in llm.schemas

    # ── 类案检索默认关闭（§五、§三十 用例 1）─────────────────────────────
    assert result["needs_case_retrieval"] is False
    assert not result.get("retrieved_cases")

    # ── 专家按计划顺序执行；重复次数由核验/修复预算决定，不在本用例口径内 ──
    assert dispatched[:2] == ["statute_retrieval_agent", "legal_consult_agent"]
    assert set(dispatched) <= {"statute_retrieval_agent", "legal_consult_agent"}
    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].content
