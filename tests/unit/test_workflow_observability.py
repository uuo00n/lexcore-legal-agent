"""Trace 时间线与工作流指标的口径（§二十四、§二十五）。

这里锁三件事：

1. **时间线不留生字**：源码里发出的每一种事件类型都必须在 ``_TIMELINE_LABELS``
   里有中文名。少一个，Admin 时间线上就会出现一行只有英文 slug 的记录，
   排查时看不出那一步做了什么。
2. **指标名与标签由 Service 独占**：节点只描述发生了什么，
   ``services.workflow_metrics`` 决定指标名与标签值。测试直接读
   ``render_prometheus()`` 的文本，因此指标名或标签一旦漂移就会失败。
3. **§二十六 的验收指标真的有数**：简单路径占比、单 Agent 工具调用次数、
   软停止原因、进模型证据条数、节点时延都从真实节点里跑出来，而不是靠
   直接调用上报函数假装有数据。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.graph_runtime import observed_node
from agent.nodes.clarification import clarification_node
from agent.nodes.complexity import complexity_router_node
from agent.nodes.evidence_normalizer import normalize_evidence
from agent.tool_loop import STOP_DUPLICATE_QUERY, admit_tool_calls, tool_call_signature
from services.metrics import render_prometheus, reset_metrics_for_tests

# 私有常量是有意暴露给测试的：标签表被提到模块级就是为了让覆盖率可断言。
from services.observability import _TIMELINE_LABELS, _summarize_event

REPO_ROOT = Path(__file__).resolve().parents[2]
# 只扫描真正发事件的三个包；测试与虚拟环境不参与统计。
EVENT_SOURCE_PACKAGES = ("agent", "api", "services")
# ``record_event(trace_id, "x", ...)`` 与 ``record_trace_event(state.get(...), "x", ...)``
# 的第二个位置参数就是事件类型；第一个参数里不含逗号，因此 ``[^,]+`` 足够。
_EVENT_CALL_RE = re.compile(r"record_(?:trace_)?event\(\s*[^,]+,\s*[\"']([a-z0-9_]+)[\"']")


def setup_function() -> None:
    reset_metrics_for_tests()


def _emitted_event_types() -> set[str]:
    found: set[str] = set()
    for package in EVENT_SOURCE_PACKAGES:
        for path in (REPO_ROOT / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            found.update(_EVENT_CALL_RE.findall(path.read_text(encoding="utf-8")))
    return found


# ─── §二十四 时间线 ──────────────────────────────────────────────────────


def test_every_emitted_trace_event_has_a_human_readable_label() -> None:
    emitted = _emitted_event_types()

    # 扫描本身要有意义：正则失效时应当立刻发现，而不是静默通过一个空集合。
    assert len(emitted) >= 40
    missing = sorted(event_type for event_type in emitted if event_type not in _TIMELINE_LABELS)
    assert missing == []
    # 标签必须是人话，不能拿 slug 充数。
    assert all(_TIMELINE_LABELS[event_type] != event_type for event_type in emitted)


def test_workflow_events_are_summarized_with_their_own_payload_fields() -> None:
    assert "simple" in _summarize_event(
        "complexity_route",
        {"complexity_level": "simple", "execution_mode": "simple", "needs_case_retrieval": False},
    )
    assert "planner_llm_unavailable" in _summarize_event(
        "planner_degraded",
        {"reason": "planner_llm_unavailable", "error": "planner provider 503"},
    )
    assert STOP_DUPLICATE_QUERY in _summarize_event(
        "tool_loop_stopped",
        {"reason": STOP_DUPLICATE_QUERY, "duplicate_tools": ["search_law_tool"]},
    )
    assert _summarize_event("evidence_normalized", {"unique_law_count": 3, "evidence_gain": 3})
    # 未知类型只能是空摘要：时间线宁可少一行说明，也不能编造内容。
    assert _summarize_event("not_a_real_event", {"whatever": 1}) == ""


# ─── §二十五 路由与澄清 ──────────────────────────────────────────────────


def test_simple_route_is_counted_with_its_execution_mode() -> None:
    complexity_router_node({
        "messages": [HumanMessage(content="公司拖欠我三个月工资怎么办")],
        "rewritten_query": "公司拖欠我三个月工资怎么办",
        "intent": "labor",
        "trace_id": "",
    })

    text = render_prometheus()
    assert (
        'legal_workflow_route_total{complexity_level="simple",'
        'execution_mode="simple",needs_case_retrieval="false"} 1.0'
    ) in text


def test_clarification_round_is_counted_as_blocking() -> None:
    clarification_node({
        "messages": [HumanMessage(content="公司辞退我，我能赔多少钱？")],
        "rewritten_query": "公司辞退我，我能赔多少钱？",
        "clarification_questions": ["你在公司工作了多久？"],
        "trace_id": "",
    })

    assert (
        'legal_workflow_clarification_total{blocking="true",outcome="required"} 1.0'
        in render_prometheus()
    )


# ─── §二十五 工具循环 ────────────────────────────────────────────────────


def _law_call(query: str = "拖欠工资") -> dict:
    return {"name": "search_law_tool", "args": {"query": query}, "id": "call_0"}


def test_admitted_tool_calls_are_counted_per_agent() -> None:
    admit_tool_calls(
        AIMessage(content="", tool_calls=[_law_call()]),
        {"tool_call_count": 0},
        agent_name="statute_retrieval_agent",
    )

    text = render_prometheus()
    assert 'legal_workflow_tool_calls_total{agent="statute_retrieval_agent"} 1.0' in text
    # 直方图用于算「单 Agent 平均工具调用次数」（§二十六 ≤2 次）。
    assert 'legal_workflow_agent_tool_calls_count{agent="statute_retrieval_agent"} 1' in text


def test_soft_stopped_tool_loop_is_counted_with_its_reason() -> None:
    call = _law_call()
    step = admit_tool_calls(
        AIMessage(content="", tool_calls=[call]),
        {"tool_call_count": 1, "tool_query_signatures": [tool_call_signature(call)]},
        agent_name="statute_retrieval_agent",
    )

    assert step.continue_loop is False
    text = render_prometheus()
    assert (
        'legal_workflow_tool_loop_stopped_total{agent="statute_retrieval_agent",'
        f'reason="{STOP_DUPLICATE_QUERY}"}} 1.0'
    ) in text
    # 软停止不放行任何调用，因此不能同时计入调用数。
    assert "legal_workflow_tool_calls_total" not in text


# ─── §二十五 证据归一化 ──────────────────────────────────────────────────


def _law_tool_message() -> ToolMessage:
    return ToolMessage(
        content=json.dumps(
            {
                "source_type": "local_rag",
                "status": "found",
                "results": [
                    {
                        "law_name": "中华人民共和国劳动合同法",
                        "article_no": "第八十五条",
                        "content": "未及时足额支付劳动报酬的，由劳动行政部门责令限期支付。",
                        "score": 0.9,
                    },
                    {
                        "law_name": "已废止的旧办法",
                        "article_no": "第一条",
                        "content": "本办法自公布之日起施行。",
                        "timeliness_name": "已废止",
                        "score": 0.8,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        tool_call_id="call_law_1",
        name="retrieve_local_law_tool",
    )


def test_normalized_evidence_reports_kept_dropped_and_gain() -> None:
    result = normalize_evidence({"messages": [_law_tool_message()], "trace_id": ""})

    assert result["evidence_gain"] == 1
    text = render_prometheus()
    assert 'legal_workflow_evidence_kept_sum{kind="law"} 1.0' in text
    assert 'legal_workflow_evidence_kept_sum{kind="case"} 0.0' in text
    assert "legal_workflow_evidence_gain_sum 1.0" in text
    # 失效法条被过滤掉这件事必须能看见，否则「检索到很多、能用的很少」无从发现。
    assert "legal_workflow_evidence_dropped_total 1.0" in text


def test_idle_normalizer_pass_does_not_pollute_the_evidence_distribution() -> None:
    normalize_evidence({"messages": [HumanMessage(content="公司拖欠工资")], "trace_id": ""})

    assert "legal_workflow_evidence_kept" not in render_prometheus()


# ─── §二十五 节点时延 ────────────────────────────────────────────────────


async def test_every_graph_node_reports_latency_and_status() -> None:
    ok = observed_node("demo_node", lambda _state: {"retrieved_laws": [{"law_name": "劳动合同法"}]})
    await ok({"trace_id": ""}, {"configurable": {}})

    text = render_prometheus()
    assert 'legal_workflow_node_total{node="demo_node",status="success"} 1.0' in text
    assert 'legal_workflow_node_latency_ms_count{node="demo_node"} 1' in text


async def test_failing_node_is_counted_as_an_error_and_still_raises() -> None:
    def boom(_state):
        raise RuntimeError("node exploded")

    failing = observed_node("boom_node", boom)
    try:
        await failing({"trace_id": ""}, {"configurable": {}})
    except RuntimeError as exc:
        assert str(exc) == "node exploded"
    else:  # pragma: no cover - 异常必须继续向上抛，否则 Graph 会静默吞掉失败
        raise AssertionError("observed_node must re-raise node failures")

    text = render_prometheus()
    assert 'legal_workflow_node_total{node="boom_node",status="error"} 1.0' in text
    assert 'legal_workflow_node_latency_ms_count{node="boom_node"} 1' in text
