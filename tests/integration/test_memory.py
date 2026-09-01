"""Layered memory integration tests for follow-up questions."""
from __future__ import annotations

import time

from langchain_core.messages import HumanMessage

from agent.nodes.memory import memory_node
from services.memory_store import MemoryItem


class FakeMemoryStore:
    def __init__(self):
        self.queries: list[tuple[str, str | None, str | None]] = []

    def search_memories(self, query, *, thread_id=None, owner_id=None, top_k=3):
        self.queries.append((query, thread_id, owner_id))
        return [MemoryItem(
            content="用户此前说明月工资为10000元，工作年限为5年。",
            memory_type="episodic",
            thread_id="memory-thread",
            created_at=int(time.time()),
        )]


def test_multi_turn_follow_up_loads_profile_summary_and_relevant_memory(monkeypatch):
    store = FakeMemoryStore()
    monkeypatch.setattr(
        "services.memory.get_user_profile",
        lambda _thread_id: {"identity": "劳动者", "focus_areas": ["劳动争议"]},
    )
    monkeypatch.setattr(
        "services.memory.get_summary",
        lambda _thread_id: "首轮咨询涉及公司口头辞退。",
    )
    monkeypatch.setattr("services.memory_store.get_memory_store", lambda: store)
    monkeypatch.setattr(
        "services.openviking_context.retrieve_agent_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("disabled in test")),
    )

    result = memory_node({
        "thread_id": "memory-thread",
        "user_id": "user-1",
        "messages": [
            HumanMessage(content="公司把我辞退了。"),
            HumanMessage(content="那赔偿金应该怎么计算？"),
        ],
    })

    assert result["memory_profile"] == "身份：劳动者\n关注领域：劳动争议"
    assert result["memory_summary"] == "首轮咨询涉及公司口头辞退。"
    assert "月工资为10000元" in result["memory_longterm"]
    assert store.queries == [("那赔偿金应该怎么计算？", None, "user-1")]


def test_memory_node_without_thread_does_not_touch_storage(monkeypatch):
    monkeypatch.setattr(
        "services.memory_store.get_memory_store",
        lambda: (_ for _ in ()).throw(AssertionError("storage must not be touched")),
    )

    assert memory_node({"messages": [HumanMessage(content="继续说") ]}) == {}
