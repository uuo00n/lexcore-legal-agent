from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage


class _Snapshot:
    def __init__(self, values):
        self.values = values


class _GraphWithState:
    def __init__(self, values):
        self.values = values

    def get_state(self, config):
        return _Snapshot(self.values)


def test_visible_history_hides_tool_call_ai_drafts():
    """
    函数作用：
        历史记录不展示模型发起工具调用时夹带的半成品文本。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    from api.threads import _visible_history_messages

    visible = _visible_history_messages([
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
        AIMessage(content="**根据《未成年人保护法》第三十九条**，学校应当及时处理。"),
    ])

    assert [item["role"] for item in visible] == ["user", "assistant"]
    assert all("让我先帮你查" not in item["content"] for item in visible)
    assert "**" not in visible[-1]["content"]


def test_history_falls_back_to_archived_messages_when_checkpoint_missing(monkeypatch):
    """
    函数作用：
        内存 checkpoint 不存在时，会话历史应从持久归档中加载。
    输入参数：
        - monkeypatch: pytest fixture
    输出参数：
        - 未标注
    """
    from api import threads as threads_api

    monkeypatch.setattr(threads_api, "load_all_messages", lambda thread_id: [
        {"role": "human", "content": "我之前说我是学生"},
        {"role": "ai", "content": "**我记住了，你是在校学生。**"},
    ])

    visible = threads_api._history_messages_for_thread(_GraphWithState({}), "thread-with-archive")

    assert visible == [
        {"role": "user", "content": "我之前说我是学生", "name": None},
        {"role": "assistant", "content": "我记住了，你是在校学生。", "name": None},
    ]
