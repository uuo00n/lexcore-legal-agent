from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.modifier import RemoveMessage


def test_context_status_reports_budget_pressure():
    from services.context_compaction import ContextCompactionConfig, build_context_status

    messages = [
        HumanMessage(content="甲" * 1500),
        AIMessage(content="乙" * 1500),
        HumanMessage(content="丙" * 1500),
    ]

    status = build_context_status(
        messages,
        config=ContextCompactionConfig(
            keep_recent=1,
            token_budget=1000,
            auto_compact_ratio=0.5,
            auto_compact_messages=20,
        ),
    )

    assert status["message_count"] == 3
    assert status["compactable_messages"] == 2
    assert status["estimated_tokens"] > 1000
    assert status["usage_ratio"] > 1
    assert status["should_compact"] is True


def test_merge_profile_entities_preserves_existing_identity():
    from services.context_compaction import merge_profile_entities

    merged = merge_profile_entities(
        {
            "identity": "员工",
            "focus_areas": ["劳动纠纷"],
            "preferences": ["先看风险"],
        },
        {
            "identity": "",
            "focus_areas": ["劳动争议", "劳动纠纷"],
            "preferences": [],
        },
        {
            "parties": ["公司", "用户"],
            "facts": ["用户工作三年"],
            "amounts": ["月工资8000元"],
        },
        open_questions=["是否签了书面劳动合同"],
        legal_focus=["经济补偿"],
    )

    assert merged["identity"] == "员工"
    assert merged["focus_areas"] == ["劳动纠纷", "劳动争议"]
    assert merged["preferences"] == ["先看风险"]
    assert merged["case_profile"]["facts"] == ["用户工作三年"]
    assert merged["case_profile"]["open_questions"] == ["是否签了书面劳动合同"]
    assert merged["case_profile"]["legal_focus"] == ["经济补偿"]


@pytest.mark.asyncio
async def test_compact_state_context_saves_summary_profile_and_removes_old_messages(monkeypatch):
    from services import context_compaction
    from services.context_compaction import ContextCompactionConfig, compact_state_context

    class FakeLLM:
        async def ainvoke(self, prompt):
            return AIMessage(content=json.dumps({
                "summary": "用户咨询劳动合同到期不续签，已说明工作三年。",
                "entities": {
                    "identity": "员工",
                    "focus_areas": ["劳动纠纷"],
                    "preferences": ["先看风险"],
                },
                "case_profile": {
                    "parties": ["用户", "公司"],
                    "facts": ["用户工作三年"],
                    "dates": [],
                    "amounts": [],
                    "documents": [],
                },
                "open_questions": ["是否签了书面劳动合同"],
                "legal_focus": ["经济补偿"],
            }, ensure_ascii=False))

    saved = {}
    monkeypatch.setattr(context_compaction, "_get_compaction_llm", lambda: FakeLLM())
    monkeypatch.setattr(context_compaction, "get_summary", lambda thread_id: "旧摘要")
    monkeypatch.setattr(
        context_compaction,
        "save_summary",
        lambda thread_id, summary, msg_count: saved.update(
            {"thread_id": thread_id, "summary": summary, "msg_count": msg_count}
        ),
    )
    monkeypatch.setattr(context_compaction, "get_user_profile", lambda thread_id: {"identity": "员工"})
    monkeypatch.setattr(
        context_compaction,
        "save_user_profile",
        lambda thread_id, profile: saved.update({"profile": profile}),
    )

    result = await compact_state_context(
        {
            "thread_id": "thread-compact",
            "messages": [
                HumanMessage(id="m1", content="我在这家公司工作三年。"),
                AIMessage(id="m2", content="这涉及劳动合同续签问题。"),
                HumanMessage(id="m3", content="合同到期公司不续签。"),
                AIMessage(id="m4", content="需要看公司是否维持条件。"),
            ],
        },
        force=True,
        config=ContextCompactionConfig(
            keep_recent=2,
            token_budget=8000,
            auto_compact_ratio=0.75,
            auto_compact_messages=20,
        ),
    )

    removed = [m.id for m in result["messages"] if isinstance(m, RemoveMessage)]
    assert removed == ["m1", "m2"]
    assert saved["summary"] == "用户咨询劳动合同到期不续签，已说明工作三年。"
    assert saved["msg_count"] == 2
    assert saved["profile"]["identity"] == "员工"
    assert saved["profile"]["case_profile"]["parties"] == ["用户", "公司"]
    assert result["context_compacted"] is True
    assert result["context_status"]["compactable_messages"] == 0


@pytest.mark.asyncio
async def test_manual_compact_thread_updates_graph_state(monkeypatch):
    from api import threads as threads_api

    class Snapshot:
        values = {
            "thread_id": "thread-api",
            "messages": [HumanMessage(id="m1", content="旧消息"), HumanMessage(id="m2", content="新消息")],
        }

    class FakeGraph:
        def __init__(self):
            self.updated = None

        def get_state(self, config):
            return Snapshot()

        async def aupdate_state(self, config, values, as_node=None):
            self.updated = {"config": config, "values": values, "as_node": as_node}

    async def fake_compact(state, *, force=False):
        assert force is True
        return {
            "messages": [RemoveMessage(id="m1")],
            "context_status": {"message_count": 1, "estimated_tokens": 10},
            "context_compacted": True,
        }

    monkeypatch.setattr(threads_api, "compact_state_context", fake_compact)

    graph = FakeGraph()
    result = await threads_api._manual_compact_thread(graph, "thread-api")

    assert result["compacted"] is True
    assert graph.updated["as_node"] == "context_compaction"
    assert isinstance(graph.updated["values"]["messages"][0], RemoveMessage)
