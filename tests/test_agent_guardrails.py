"""Agent 工具注册与引用守门测试。"""
from __future__ import annotations

import json
import sys
import types

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def _stub_mcp_client() -> None:
    """
    函数作用：
        隔离 MCP 运行时依赖，让测试聚焦 Agent 层行为。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    return


def test_agent_exposes_all_mcp_tools():
    """
    函数作用：
        Agent 应暴露 MCP Server 已开放的业务工具。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    _stub_mcp_client()
    from agent.tools import ALL_TOOLS

    tool_names = {tool.name for tool in ALL_TOOLS}

    assert tool_names == {
        "law_compare_tool",
        "risk_assess_tool",
        "contract_review_tool",
        "statute_of_limitations_tool",
        "jurisdiction_tool",
        "legal_document_draft_tool",
        "retrieve_local_law_tool",
        "search_law_tool",
        "search_case_tool",
    }


def test_legal_consult_agent_has_trusted_source_tools():
    """
    函数作用：
        法律咨询专家只可查询本地法库与得理法规。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    from agent.tools import LEGAL_CONSULT_TOOLS

    tool_names = [tool.name for tool in LEGAL_CONSULT_TOOLS]

    assert tool_names == ["search_law_tool", "retrieve_local_law_tool"]


def test_specialist_tool_loop_default_is_five():
    """
    函数作用：
        每个 Specialist 任务的工具调用默认上限应为 5，避免无限循环。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    from agent.nodes import MAX_TOOL_CALLS

    assert MAX_TOOL_CALLS == 5


def test_guard_law_citations_removes_unretrieved_reference():
    """
    函数作用：
        未出现在本轮检索结果里的法条引用不应原样留在最终回答中。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    _stub_mcp_client()
    from agent.nodes import _guard_law_citations

    content = (
        "可以主张违约责任，依据《民法典》第五百七十七条。"
        "另外还可以依据《刑法》第二百六十四条处理。"
    )
    retrieved_laws = [
        {"law_name": "民法典", "article_no": "第五百七十七条"},
    ]

    guarded = _guard_law_citations(content, retrieved_laws)

    assert "《民法典》第五百七十七条" in guarded
    assert "《刑法》第二百六十四条" not in guarded
    assert "未在本轮检索结果中确认" in guarded


def test_prompt_requires_plain_inline_law_format():
    """
    函数作用：
        法律咨询提示词应约束为自然段回答，避免 Markdown 报告格式。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    from agent.prompts import LEGAL_SYSTEM_PROMPT, LEGAL_SYSTEM_PROMPT_NO_TOOLS, SUPERVISOR_FINAL_PROMPT

    for prompt in (LEGAL_SYSTEM_PROMPT, LEGAL_SYSTEM_PROMPT_NO_TOOLS):
        assert "只输出 JSON" in prompt
        assert "不要输出最终用户回答" in prompt
        assert "legal_consult_agent" in prompt
    assert "retrieve_local_law_tool 最多调用一次" in LEGAL_SYSTEM_PROMPT
    assert "evidence_insufficient=true" in LEGAL_SYSTEM_PROMPT
    assert "web_search_tool" not in LEGAL_SYSTEM_PROMPT
    assert "本 Agent 不调用对应工具" in LEGAL_SYSTEM_PROMPT
    assert "不要使用 Markdown 标题" in SUPERVISOR_FINAL_PROMPT
    assert "可以用短段落和简单编号换行" in SUPERVISOR_FINAL_PROMPT


def test_supervisor_prompt_requires_article_specific_why_format():
    """
    函数作用：
        主控最终回答的“为什么”部分应优先按具体法条逐句说明，避免只写法律名称。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    from agent.prompts import SUPERVISOR_FINAL_PROMPT

    assert "为什么：" in SUPERVISOR_FINAL_PROMPT
    assert "根据《法律名称》第X条" in SUPERVISOR_FINAL_PROMPT
    assert "不要只写“根据《民法典》”而省略条号" in SUPERVISOR_FINAL_PROMPT


class _FakeFinalLLM:
    """返回固定最终回答的最小 LLM 替身。"""

    def __init__(self, content: str):
        self.content = content
        self.bound_tool_names: list[str] = []

    def bind_tools(self, tools):
        self.bound_tool_names = [tool.name for tool in tools]
        return self

    async def ainvoke(self, messages):
        return AIMessage(content=self.content)


async def test_legal_consult_answer_does_not_append_unmentioned_law_sources(monkeypatch):
    """
    函数作用：
        最终回答只保留正文实际引用且被检索支撑的法条，不追加整包检索结果。
    输入参数：
        - monkeypatch: pytest fixture
    输出参数：
        - 未标注
    """
    _stub_mcp_client()
    from agent import nodes

    monkeypatch.setattr(nodes, "get_llm", lambda **kwargs: _FakeFinalLLM(
        "根据《未成年人保护法》第三十九条，学校应当及时制止并通知监护人参与处理。"
    ))
    monkeypatch.setattr(nodes, "supports_tools", lambda provider=None: False)
    monkeypatch.setattr(nodes, "search_similar_cases", lambda query: [])

    retrieved_laws = [
        {"law_name": "未成年人保护法", "article_no": "第三十九条", "content": "学校应当建立学生欺凌防控工作制度。"},
        {"law_name": "反间谍法", "article_no": "第三十条", "content": "无关内容。"},
    ]
    result = await nodes.legal_consult_agent_node({
        "messages": [
            HumanMessage(content="同学威胁我要50块钱怎么办"),
            ToolMessage(
                content=json.dumps({"results": retrieved_laws}, ensure_ascii=False),
                tool_call_id="call_1",
            ),
        ],
        "retrieved_laws": retrieved_laws,
    })

    report = result["agent_reports"][0]
    content = report["analysis"]

    assert "根据《未成年人保护法》第三十九条" in content
    assert "【引用法条】" not in content
    assert "反间谍法" not in content
    assert "messages" not in result


async def test_legal_consult_agent_does_not_bind_local_search_after_retrieval(monkeypatch):
    """
    函数作用：
        本地法条检索已执行后，法律咨询专家不应再次绑定本地检索工具。
    输入参数：
        - monkeypatch: pytest fixture
    输出参数：
        - 未标注
    """
    _stub_mcp_client()
    from agent import nodes

    fake_llm = _FakeFinalLLM(
        '{"agent":"legal_consult_agent","status":"analysis_ready","legal_issues":[],"law_basis":[],"analysis":"基于已有检索结果分析。","risks":[],"next_steps":[],"confidence":"medium"}'
    )

    monkeypatch.setattr(nodes, "get_llm", lambda **kwargs: fake_llm)
    monkeypatch.setattr(nodes, "supports_tools", lambda provider=None: True)
    monkeypatch.setattr(nodes, "search_similar_cases", lambda query: [])

    retrieved_laws = [
        {"law_name": "劳动合同法", "article_no": "第四十六条", "content": "有下列情形之一的，用人单位应当向劳动者支付经济补偿。"},
    ]
    await nodes.legal_consult_agent_node({
        "messages": [
            HumanMessage(content="劳动合同到期不续签有补偿吗"),
            ToolMessage(
                content=json.dumps({"results": retrieved_laws}, ensure_ascii=False),
                tool_call_id="call_1",
            ),
        ],
        "retrieved_laws": retrieved_laws,
        "tool_call_count": 1,
    })

    assert "retrieve_local_law_tool" not in fake_llm.bound_tool_names
    assert "search_law_tool" in fake_llm.bound_tool_names
    assert set(fake_llm.bound_tool_names) == {"search_law_tool"}


async def test_legal_consult_agent_keeps_local_search_after_unrelated_tool_observation(monkeypatch):
    """
    函数作用：
        管辖路径工具调用后，不应误判为本地法条检索已执行。
    输入参数：
        - monkeypatch: pytest fixture
    输出参数：
        - 未标注
    """
    _stub_mcp_client()
    from agent import nodes

    fake_llm = _FakeFinalLLM(
        '{"agent":"legal_consult_agent","status":"analysis_ready","legal_issues":[],"law_basis":[],"analysis":"先判断办理路径，再补充法条。","risks":[],"next_steps":[],"confidence":"medium"}'
    )

    monkeypatch.setattr(nodes, "get_llm", lambda **kwargs: fake_llm)
    monkeypatch.setattr(nodes, "supports_tools", lambda provider=None: True)
    monkeypatch.setattr(nodes, "search_similar_cases", lambda query: [])

    await nodes.legal_consult_agent_node({
        "messages": [
            HumanMessage(content="劳动仲裁去哪里申请？"),
            ToolMessage(
                content=json.dumps({"case_category": "labor", "routes": []}, ensure_ascii=False),
                tool_call_id="call_1",
                name="jurisdiction_tool",
            ),
        ],
        "retrieved_laws": [],
        "tool_call_count": 1,
    })

    assert "retrieve_local_law_tool" in fake_llm.bound_tool_names
    assert "search_law_tool" in fake_llm.bound_tool_names


async def test_legal_consult_answer_strips_markdown_markers(monkeypatch):
    """
    函数作用：
        即使模型输出 Markdown，最终给用户的法律咨询回答也不应出现 ** 或标题符号。
    输入参数：
        - monkeypatch: pytest fixture
    输出参数：
        - 未标注
    """
    _stub_mcp_client()
    from agent import nodes

    monkeypatch.setattr(nodes, "get_llm", lambda **kwargs: _FakeFinalLLM(
        "**根据《未成年人保护法》第三十九条**，学校应当及时处理。\n\n## 处理建议\n请先告诉老师。"
    ))
    monkeypatch.setattr(nodes, "supports_tools", lambda provider=None: False)
    monkeypatch.setattr(nodes, "search_similar_cases", lambda query: [])

    retrieved_laws = [
        {"law_name": "未成年人保护法", "article_no": "第三十九条", "content": "学校应当建立学生欺凌防控工作制度。"},
    ]
    result = await nodes.legal_consult_agent_node({
        "messages": [
            HumanMessage(content="同学威胁我要50块钱怎么办"),
            ToolMessage(
                content=json.dumps({"results": retrieved_laws}, ensure_ascii=False),
                tool_call_id="call_1",
            ),
        ],
        "retrieved_laws": retrieved_laws,
    })

    report = result["agent_reports"][0]
    content = report["analysis"]

    assert "**" not in content
    assert "##" not in content
    assert content.startswith("根据《未成年人保护法》第三十九条")
    assert "messages" not in result
