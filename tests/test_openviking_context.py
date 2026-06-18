from __future__ import annotations

from langchain_core.messages import HumanMessage

from agent.nodes import memory_node
from services.openviking_client import OpenVikingMatch
from services.openviking_context import OpenVikingContextConfig, retrieve_real_openviking_context


class FakeOpenVikingClient:
    def __init__(self):
        self.calls = []
        self.closed = False

    def find(self, query, *, target_uri="", context_type=None, limit=10, score_threshold=None, level=None):
        self.calls.append({
            "query": query,
            "target_uri": target_uri,
            "context_type": context_type,
            "limit": limit,
            "score_threshold": score_threshold,
            "level": level,
        })
        if context_type == "resource":
            return [
                OpenVikingMatch(
                    uri="viking://resources/laws/labor/劳动合同法/劳动合同法_第二十条.md",
                    context_type="resource",
                    score=0.91,
                    abstract="试用期工资和劳动合同履行规则。",
                    overview="用于判断试用期、工资、解除劳动关系等劳动争议。",
                    level=1,
                )
            ]
        if context_type == "skill":
            return [
                OpenVikingMatch(
                    uri="viking://agent/skills/labor-arbitration-workflow/SKILL.md",
                    context_type="skill",
                    score=0.84,
                    abstract="劳动仲裁咨询流程。",
                    overview="先补充劳动关系、入职时间、工资、解除理由和证据。",
                    level=1,
                )
            ]
        return []

    def close(self):
        self.closed = True


def test_retrieve_real_openviking_context_formats_resource_and_skill_hits():
    client = FakeOpenVikingClient()

    result = retrieve_real_openviking_context(
        "试用期被公司辞退，能仲裁吗？",
        client=client,
        config=OpenVikingContextConfig(
            enabled=True,
            resource_target_uri="viking://resources/laws",
            skill_target_uri="",
            resource_limit=4,
            skill_limit=3,
            timeout=2.0,
            score_threshold=0.5,
        ),
    )

    assert len(result.hits) == 2
    assert "真实 OpenViking Context Database" in result.prompt
    assert "viking://resources/laws/labor/劳动合同法/劳动合同法_第二十条.md" in result.prompt
    assert "viking://agent/skills/labor-arbitration-workflow/SKILL.md" in result.prompt
    assert "法条引用仍必须以本轮法律检索工具结果为准" in result.prompt
    assert client.calls[0]["context_type"] == "resource"
    assert client.calls[0]["level"] == [0, 1, 2]
    assert client.calls[1]["context_type"] == "skill"


def test_retrieve_real_openviking_context_filters_skills_by_resource_domain():
    class DomainFilteringClient(FakeOpenVikingClient):
        def find(self, query, *, target_uri="", context_type=None, limit=10, score_threshold=None, level=None):
            self.calls.append({"context_type": context_type})
            if context_type == "resource":
                return [
                    OpenVikingMatch(
                        uri="viking://resources/laws/labor/劳动合同法/劳动合同法_第二十条.md",
                        context_type="resource",
                        score=0.91,
                        abstract="试用期工资和劳动合同履行规则。",
                        level=2,
                    )
                ]
            if context_type == "skill":
                return [
                    OpenVikingMatch(
                        uri="viking://agent/default/skills/labor-arbitration-workflow/.abstract.md",
                        context_type="skill",
                        score=0.58,
                        abstract="tags: legal labor arbitration 劳动仲裁咨询流程。",
                        level=0,
                    ),
                    OpenVikingMatch(
                        uri="viking://agent/default/skills/deposit-dispute-workflow/.abstract.md",
                        context_type="skill",
                        score=0.56,
                        abstract="tags: legal contract lease 押金退还纠纷流程。",
                        level=0,
                    ),
                ]
            return []

    result = retrieve_real_openviking_context(
        "试用期被公司辞退，能仲裁吗？",
        client=DomainFilteringClient(),
        config=OpenVikingContextConfig(enabled=True),
    )

    skill_uris = [hit.uri for hit in result.hits if hit.context_type == "skill"]
    assert skill_uris == [
        "viking://agent/default/skills/labor-arbitration-workflow/.abstract.md"
    ]
    assert "deposit-dispute-workflow" not in result.prompt


def test_memory_node_prefers_real_openviking_context(monkeypatch):
    monkeypatch.setattr(
        "services.memory.get_user_profile",
        lambda thread_id: {"identity": "员工", "focus_areas": ["劳动纠纷"]},
    )
    monkeypatch.setattr("services.memory.get_summary", lambda thread_id: "用户咨询试用期辞退。")

    class EmptyMemoryStore:
        def search_memories(self, query: str, top_k: int = 3):
            return []

    monkeypatch.setattr("services.memory_store.get_memory_store", lambda: EmptyMemoryStore())

    fake_client = FakeOpenVikingClient()
    monkeypatch.setattr("services.openviking_context.OpenVikingHTTPClient", lambda settings: fake_client)
    monkeypatch.setenv("OPENVIKING_CONTEXT_ENABLED", "true")
    monkeypatch.setenv("OPENVIKING_CONTEXT_TIMEOUT", "2")

    result = memory_node({
        "thread_id": "thread-001",
        "messages": [HumanMessage(content="试用期被公司辞退，能仲裁吗？")],
    })

    assert "真实 OpenViking Context Database" in result["viking_context"]
    assert any(hit["context_type"] == "resource" for hit in result["viking_context_hits"])
    assert any(hit["context_type"] == "skill" for hit in result["viking_context_hits"])
    assert fake_client.closed is True
