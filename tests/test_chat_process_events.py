"""聊天 SSE 处理过程事件测试。

这些测试锁定前端可见的“处理过程”语义：系统可以展示当前执行阶段，
但不能把模型在工具调用消息里附带的自由文本当作内部推理展示给用户。
"""
from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from api.chat import ChatRequest, _event_stream


class _FakeGraph:
    """最小化 LangGraph 替身，用于验证 SSE 事件转换行为。"""

    async def astream(self, state_input, config, stream_mode):
        yield {
            "legal_consult_agent": {
                "messages": [
                    AIMessage(
                        content="这里是模型随工具调用产生的内部分析，不应该展示给用户。",
                        tool_calls=[
                            {
                                "name": "retrieve_local_law_tool",
                                "args": {"query": "校园霸凌 学生欺凌"},
                                "id": "call_1",
                            }
                        ],
                    )
                ]
            }
        }
        yield {
            "legal_consult_agent": {
                "messages": [
                    AIMessage(content="【先说结论】你应先保证安全，并保存证据。")
                ]
            }
        }


class _Snapshot:
    def __init__(self, values):
        self.values = values


class _GraphWithState:
    def __init__(self, values):
        self.values = values

    def get_state(self, config):
        return _Snapshot(self.values)


class _GraphThatShouldNotStart:
    def get_state(self, config):
        return _Snapshot({})

    async def astream(self, state_input, config, stream_mode):
        raise AssertionError("graph execution should not start before initial thought event")


class _GraphWithSupervisorFinal:
    """专家报告不应直接流给用户，主控最终消息才是最终答案。"""

    async def astream(self, state_input, config, stream_mode):
        yield {
            "legal_consult_agent": {
                "agent_reports": [
                    {
                        "agent": "legal_consult_agent",
                        "status": "analysis_ready",
                        "analysis": "这是专家报告，不应直接展示。",
                    }
                ]
            }
        }
        yield {
            "supervisor_agent": {
                "messages": [AIMessage(content="这是主控整理后的最终回答。")]
            }
        }


class _GraphWithLegalConsultTextOnly:
    """专家节点文本不能绕过主控成为最终答案。"""

    async def astream(self, state_input, config, stream_mode):
        yield {
            "legal_consult_agent": {
                "messages": [AIMessage(content="根据刑法规定，非法种植罂粟需要区分治安违法和犯罪门槛。")]
            }
        }
        yield {
            "supervisor_agent": {
                "supervisor_route": "legal_consult_agent",
                "supervisor_reason": "继续咨询",
            }
        }


class _GraphWithContextStatus:
    async def astream(self, state_input, config, stream_mode):
        yield {
            "context_compaction": {
                "context_status": {
                    "message_count": 12,
                    "estimated_tokens": 6000,
                    "token_budget": 8000,
                    "usage_ratio": 0.75,
                    "should_compact": False,
                }
            }
        }
        yield {
            "supervisor_agent": {
                "messages": [AIMessage(content="上下文状态已更新。")]
            }
        }


def test_build_state_input_restores_archived_history_when_checkpoint_missing(monkeypatch):
    from api import chat as chat_api

    monkeypatch.setattr(chat_api, "load_all_messages", lambda thread_id: [
        {"role": "human", "content": "我之前说我是学生"},
        {"role": "ai", "content": "我记住了，你是在校学生。"},
    ])

    req = ChatRequest(thread_id="thread-with-archive", message="那我现在被同学威胁怎么办？")
    state_input = chat_api._build_state_input(
        _GraphWithState({}),
        req,
        doc_text=None,
        doc_name=None,
        trace_id="trace-1",
    )

    assert [m.content for m in state_input["messages"]] == [
        "我之前说我是学生",
        "我记住了，你是在校学生。",
        "那我现在被同学威胁怎么办？",
    ]


def test_build_state_input_uses_checkpoint_when_available(monkeypatch):
    from api import chat as chat_api

    monkeypatch.setattr(chat_api, "load_all_messages", lambda thread_id: [
        {"role": "human", "content": "归档里的旧消息不应重复塞入"},
    ])

    req = ChatRequest(thread_id="thread-with-checkpoint", message="继续刚才的问题")
    state_input = chat_api._build_state_input(
        _GraphWithState({"messages": [HumanMessage(content="内存里已有历史")]}),
        req,
        doc_text=None,
        doc_name=None,
        trace_id="trace-2",
    )

    assert [m.content for m in state_input["messages"]] == ["继续刚才的问题"]


@pytest.mark.asyncio
async def test_process_event_hides_model_internal_text_when_tool_call_has_content(monkeypatch):
    monkeypatch.setattr("api.chat.create_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.record_event", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.complete_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.inc_counter", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.observe", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.get_cached_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.set_cached_answer", lambda *args, **kwargs: None)

    req = ChatRequest(
        thread_id="test-process-event-thread",
        message="我被校园霸凌了怎么办？",
    )

    events = [event async for event in _event_stream(_FakeGraph(), req)]
    thought_payloads = [
        json.loads(event["data"])
        for event in events
        if event["event"] == "thought"
    ]

    assert thought_payloads
    assert all("内部分析" not in item["content"] for item in thought_payloads)
    assert any("正在" in item["content"] for item in thought_payloads)


@pytest.mark.asyncio
async def test_event_stream_emits_context_status(monkeypatch):
    monkeypatch.setattr("api.chat.create_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.record_event", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.complete_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.inc_counter", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.observe", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.get_cached_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.set_cached_answer", lambda *args, **kwargs: None)

    events = [
        event
        async for event in _event_stream(
            _GraphWithContextStatus(),
            ChatRequest(thread_id="thread-context-status", message="继续"),
        )
    ]
    payloads = [
        json.loads(event["data"])
        for event in events
        if event["event"] == "context_status"
    ]

    assert payloads
    assert payloads[0]["usage_ratio"] == 0.75


@pytest.mark.asyncio
async def test_event_stream_emits_initial_thought_before_graph_execution(monkeypatch):
    monkeypatch.setattr("api.chat.create_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.record_event", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.complete_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.inc_counter", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.observe", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.get_cached_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.set_cached_answer", lambda *args, **kwargs: None)

    req = ChatRequest(thread_id="test-initial-thought-thread", message="房东不退押金怎么办？")

    stream = _event_stream(_GraphThatShouldNotStart(), req)
    first_event = await anext(stream)
    await stream.aclose()

    assert first_event["event"] == "thought"
    payload = json.loads(first_event["data"])
    assert "正在" in payload["content"]


@pytest.mark.asyncio
async def test_event_stream_uses_supervisor_final_answer(monkeypatch):
    monkeypatch.setattr("api.chat.create_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.record_event", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.complete_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.inc_counter", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.observe", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.get_cached_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.set_cached_answer", lambda *args, **kwargs: None)

    req = ChatRequest(
        thread_id="test-supervisor-final-thread",
        message="劳动合同到期公司不续签怎么办？",
    )

    events = [event async for event in _event_stream(_GraphWithSupervisorFinal(), req)]
    streamed = "".join(event["data"] for event in events if event["event"] == "token")

    assert streamed == "这是主控整理后的最终回答。"
    assert "专家报告" not in streamed


@pytest.mark.asyncio
async def test_event_stream_does_not_use_legal_consult_text_without_supervisor_final(monkeypatch):
    monkeypatch.setattr("api.chat.create_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.record_event", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.complete_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.inc_counter", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.observe", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.get_cached_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr("api.chat.set_cached_answer", lambda *args, **kwargs: None)

    req = ChatRequest(
        thread_id="test-legal-consult-final-thread",
        message="种植罂粟几株犯法",
    )

    events = [event async for event in _event_stream(_GraphWithLegalConsultTextOnly(), req)]
    streamed = "".join(event["data"] for event in events if event["event"] == "token")

    assert streamed == ""
