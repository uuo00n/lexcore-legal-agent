from __future__ import annotations

from langchain_core.messages import HumanMessage

from agent.nodes import legal_consult_agent_node, memory_node
from services.model_routing import ModelRoute
from services.viking_context import retrieve_viking_context, save_case_workspace


def test_retrieve_context_groups_resource_memory_and_skill():
    result = retrieve_viking_context(
        "公司试用期把我辞退了，能不能申请劳动仲裁？",
        thread_id="thread-001",
        profile="身份：员工",
        summary="用户持续咨询劳动纠纷。",
        longterm="- [episodic] 用户被公司在试用期解除劳动关系。",
    )

    assert any(hit.context_type == "resource" and "labor" in hit.uri for hit in result.hits)
    assert any(hit.context_type == "skill" and "labor_arbitration" in hit.uri for hit in result.hits)
    assert any(hit.context_type == "memory" and "thread-001" in hit.uri for hit in result.hits)
    assert "viking://" in result.prompt
    assert "Resource / Memory / Skill" in result.prompt
    assert "不作为法条引用依据" in result.prompt


def test_save_case_workspace_writes_openviking_style_layers(tmp_path):
    save_case_workspace(
        "thread/with unsafe chars",
        [
            {"role": "human", "content": "房东不退押金怎么办？"},
            {"role": "ai", "content": "请补充合同约定、退租交接和证据情况。"},
        ],
        root=tmp_path,
    )

    case_dir = tmp_path / "memory" / "cases" / "thread_with_unsafe_chars"
    assert (case_dir / ".abstract.md").exists()
    assert (case_dir / ".overview.md").exists()
    assert (case_dir / "conversation.md").exists()
    assert "房东不退押金" in (case_dir / ".overview.md").read_text(encoding="utf-8")


def test_memory_node_injects_viking_context(monkeypatch):
    monkeypatch.setattr(
        "services.memory.get_user_profile",
        lambda thread_id: {"identity": "员工", "focus_areas": ["劳动纠纷"]},
    )
    monkeypatch.setattr("services.memory.get_summary", lambda thread_id: "用户咨询试用期辞退。")

    class EmptyMemoryStore:
        def search_memories(self, query: str, top_k: int = 3):
            return []

    monkeypatch.setattr("services.memory_store.get_memory_store", lambda: EmptyMemoryStore())

    result = memory_node({
        "thread_id": "thread-001",
        "messages": [HumanMessage(content="试用期被公司辞退，能仲裁吗？")],
    })

    assert "viking_context" in result
    assert "viking_context_hits" in result
    assert any(hit["context_type"] == "skill" for hit in result["viking_context_hits"])


async def test_legal_consult_agent_injects_viking_context_into_system_prompt(monkeypatch):
    captured = {}

    class CaptureLLM:
        async def ainvoke(self, messages):
            captured["system_prompt"] = messages[0].content
            from langchain_core.messages import AIMessage
            return AIMessage(content="测试回答")

    monkeypatch.setattr("agent.nodes.supports_tools", lambda provider=None: False)
    monkeypatch.setattr("agent.nodes.get_llm", lambda **kwargs: CaptureLLM())
    monkeypatch.setattr("agent.nodes.search_similar_cases", lambda query: [])
    monkeypatch.setattr(
        "agent.nodes.select_model_route",
        lambda **kwargs: ModelRoute(
            name="fast",
            provider=None,
            model=None,
            reason="test",
            complexity_score=1,
        ),
    )

    result = await legal_consult_agent_node({
        "messages": [HumanMessage(content="试用期被辞退怎么办？")],
        "viking_context": "## OpenViking Context Layer（Resource / Memory / Skill）\n- URI: viking://skills/legal/labor_arbitration_workflow/",
    })

    assert "messages" not in result
    assert result["agent_reports"][0]["analysis"] == "测试回答"
    assert "OpenViking Context Layer" in captured["system_prompt"]
    assert "viking://skills/legal/labor_arbitration_workflow/" in captured["system_prompt"]
