from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage


def test_messages_to_dicts_skips_tool_call_ai_drafts():
    """
    函数作用：
        记忆归档不保存工具调用阶段夹带的 AI 草稿文本。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    from services.memory_extractor import _messages_to_dicts

    result = _messages_to_dicts([
        HumanMessage(content="同学威胁我要50块钱怎么办"),
        AIMessage(
            content="让我先帮你查一下相关法律规定：",
            tool_calls=[
                {
                    "name": "legal_search_tool",
                    "args": {"query": "学生欺凌 勒索 威胁"},
                    "id": "call_1",
                }
            ],
        ),
        AIMessage(content="根据《未成年人保护法》第三十九条，学校应当及时处理。"),
    ])

    assert result == [
        {"role": "human", "content": "同学威胁我要50块钱怎么办"},
        {"role": "ai", "content": "根据《未成年人保护法》第三十九条，学校应当及时处理。"},
    ]


def test_new_messages_for_archive_appends_only_unseen_suffix(monkeypatch):
    """
    函数作用：
        后台记忆归档只追加本轮新增消息，不重复写入已归档历史。
    输入参数：
        - monkeypatch: pytest fixture
    输出参数：
        - 未标注
    """
    from services import memory_extractor

    monkeypatch.setattr(memory_extractor, "load_all_messages", lambda thread_id: [
        {"role": "human", "content": "我之前说我是学生"},
        {"role": "ai", "content": "我记住了，你是在校学生。"},
    ])

    current_messages = [
        {"role": "human", "content": "我之前说我是学生"},
        {"role": "ai", "content": "我记住了，你是在校学生。"},
        {"role": "human", "content": "那我现在被同学威胁怎么办？"},
        {"role": "ai", "content": "根据《未成年人保护法》第三十九条，学校应当处理。"},
    ]

    assert memory_extractor._new_messages_for_archive("thread-with-archive", current_messages) == [
        {"role": "human", "content": "那我现在被同学威胁怎么办？"},
        {"role": "ai", "content": "根据《未成年人保护法》第三十九条，学校应当处理。"},
    ]


async def test_background_memory_llm_uses_air_model(monkeypatch):
    """
    函数作用：
        后台摘要、长期记忆和用户画像提取应使用轻量 GLM Air 模型，避免抢占主问答模型限额。
    输入参数：
        - monkeypatch: pytest fixture
    输出参数：
        - 未标注
    """
    from services import memory_extractor

    calls = []

    class FakeLLM:
        async def ainvoke(self, prompt):
            if "返回 JSON 数组" in prompt:
                return AIMessage(content='[{"content":"用户咨询劳动问题","type":"episodic"}]')
            if "用户画像" in prompt:
                return AIMessage(content='{"identity":"","focus_areas":["劳动"],"preferences":[]}')
            return AIMessage(content="更新后的摘要")

    class FakeMemoryStore:
        def add_memory(self, **kwargs):
            pass

    def fake_get_llm(**kwargs):
        calls.append(kwargs)
        return FakeLLM()

    archived_messages = [
        {"role": "human", "content": "第一条"},
        {"role": "ai", "content": "第二条"},
        {"role": "human", "content": "第三条"},
        {"role": "ai", "content": "第四条"},
    ]

    monkeypatch.setattr(memory_extractor, "SLIDING_WINDOW_SIZE", 2)
    monkeypatch.setattr(memory_extractor, "_new_messages_for_archive", lambda thread_id, msg_dicts: msg_dicts)
    monkeypatch.setattr(memory_extractor, "save_messages", lambda *args, **kwargs: None)
    monkeypatch.setattr(memory_extractor, "load_all_messages", lambda thread_id: archived_messages)
    monkeypatch.setattr(memory_extractor, "get_summary", lambda thread_id: None)
    monkeypatch.setattr(memory_extractor, "get_summary_msg_count", lambda thread_id: 0)
    monkeypatch.setattr(memory_extractor, "save_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(memory_extractor, "get_memory_store", lambda: FakeMemoryStore())
    monkeypatch.setattr(memory_extractor, "get_user_profile", lambda thread_id: {})
    monkeypatch.setattr(memory_extractor, "save_user_profile", lambda *args, **kwargs: None)
    monkeypatch.setattr(memory_extractor, "get_llm", fake_get_llm)

    await memory_extractor.extract_and_save_memory(
        "thread-air-model",
        [
            HumanMessage(content="我月工资3000"),
            AIMessage(content="可以先保留工资流水。"),
        ],
    )

    assert len(calls) == 3
    assert all(call["model"] == "glm-4.5-air" for call in calls)
