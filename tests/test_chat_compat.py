"""思考模式 Provider 兼容层回归测试。

锁住两条口径：带 tool_calls 的 assistant 消息必须把 reasoning_content 原样带回；
结构化输出不能用 json_schema（DeepSeek 不支持）也不能在开思考时强制 tool_choice。
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from services.chat_compat import (
    COMPAT_STRUCTURED_METHOD,
    NO_THINKING_EFFORT,
    REASONING_KEY,
    CompatChatOpenAI,
)


class _Schema(BaseModel):
    answer: str


def _client(**overrides) -> CompatChatOpenAI:
    params = {
        "model": "deepseek-v4-pro",
        "base_url": "https://api.deepseek.com",
        "api_key": "test-key",
        "temperature": 0,
        "streaming": False,
        "thinking_compat": True,
    }
    params.update(overrides)
    return CompatChatOpenAI(**params)


def _response(reasoning: str | None) -> dict:
    message: dict = {"role": "assistant", "content": "好"}
    if reasoning is not None:
        message[REASONING_KEY] = reasoning
    return {
        "id": "chatcmpl-test",
        "model": "deepseek-v4-pro",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _tool_call_history(reasoning: str | None) -> list:
    additional_kwargs = {REASONING_KEY: reasoning} if reasoning is not None else {}
    return [
        SystemMessage(content="你是法条检索助手。"),
        HumanMessage(content="劳动合同法试用期规定"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call_0", "name": "search_law_tool", "args": {"query": "试用期"}}],
            additional_kwargs=additional_kwargs,
        ),
        ToolMessage(content="第十九条：试用期最长六个月。", tool_call_id="call_0"),
    ]


def test_reasoning_content_is_captured_into_additional_kwargs():
    result = _client()._create_chat_result(_response("先定位劳动合同法。"))
    assert result.generations[0].message.additional_kwargs[REASONING_KEY] == "先定位劳动合同法。"


def test_missing_reasoning_content_adds_no_key():
    result = _client()._create_chat_result(_response(None))
    assert REASONING_KEY not in result.generations[0].message.additional_kwargs


def test_reasoning_content_is_echoed_back_on_the_assistant_turn():
    payload = _client()._get_request_payload(_tool_call_history("先定位劳动合同法。"))
    assistant = [item for item in payload["messages"] if item.get("role") == "assistant"]
    assert len(assistant) == 1
    # 缺这个字段 DeepSeek 会 400：reasoning_content must be passed back to the API。
    assert assistant[0][REASONING_KEY] == "先定位劳动合同法。"


def test_payload_is_untouched_when_nothing_was_captured():
    payload = _client()._get_request_payload(_tool_call_history(None))
    assistant = [item for item in payload["messages"] if item.get("role") == "assistant"]
    assert REASONING_KEY not in assistant[0]


def test_reasoning_content_maps_to_the_matching_assistant_turn():
    messages = [
        HumanMessage(content="第一问"),
        AIMessage(content="第一答", additional_kwargs={REASONING_KEY: "思考一"}),
        HumanMessage(content="第二问"),
        AIMessage(content="第二答", additional_kwargs={REASONING_KEY: "思考二"}),
        HumanMessage(content="第三问"),
    ]
    payload = _client()._get_request_payload(messages)
    assistant = [item for item in payload["messages"] if item.get("role") == "assistant"]
    assert [item[REASONING_KEY] for item in assistant] == ["思考一", "思考二"]


def test_structured_output_drops_json_schema_and_disables_thinking(monkeypatch):
    captured: dict = {}

    def _fake(self, schema=None, *, method="json_schema", **kwargs):
        captured["model"] = self
        captured["method"] = method
        return "runnable"

    monkeypatch.setattr(ChatOpenAI, "with_structured_output", _fake)
    assert _client().with_structured_output(_Schema) == "runnable"
    # json_schema → DeepSeek 400 response_format unavailable；
    # function_calling 开思考 → 400 thinking mode does not support this tool_choice。
    assert captured["method"] == COMPAT_STRUCTURED_METHOD
    assert captured["model"].reasoning_effort == NO_THINKING_EFFORT


def test_structured_output_keeps_stock_behaviour_without_compat(monkeypatch):
    captured: dict = {}

    def _fake(self, schema=None, *, method="json_schema", **kwargs):
        captured["model"] = self
        captured["method"] = method
        return "runnable"

    monkeypatch.setattr(ChatOpenAI, "with_structured_output", _fake)
    client = _client(thinking_compat=False)
    client.with_structured_output(_Schema)
    assert captured["method"] == "json_schema"
    assert captured["model"] is client


def test_explicit_method_is_respected(monkeypatch):
    captured: dict = {}

    def _fake(self, schema=None, *, method="json_schema", **kwargs):
        captured["model"] = self
        captured["method"] = method
        return "runnable"

    monkeypatch.setattr(ChatOpenAI, "with_structured_output", _fake)
    _client().with_structured_output(_Schema, method="json_mode")
    assert captured["method"] == "json_mode"
    assert captured["model"].reasoning_effort is None


def test_deepseek_clients_are_built_with_thinking_compat(monkeypatch):
    from services import llm as llm_module

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("LLM_BASE_URL_OVERRIDE", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    config = llm_module._build_chat_client("deepseek", {}, model_route="planner")
    assert isinstance(config.client, CompatChatOpenAI)
    assert config.client.thinking_compat is True


@pytest.mark.parametrize("provider", ["zhipu", "qwen", "ollama"])
def test_other_providers_keep_stock_structured_output(monkeypatch, provider):
    from services import llm as llm_module

    monkeypatch.setenv("ZHIPU_API_KEY", "test-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.delenv("LLM_BASE_URL_OVERRIDE", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    config = llm_module._build_chat_client(provider, {})
    assert config.client.thinking_compat is False
