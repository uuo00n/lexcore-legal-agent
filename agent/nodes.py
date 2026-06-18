"""LangGraph 节点函数 —— 定义图中每个节点的执行逻辑。

包含节点：
- memory_node: 记忆加载（短期摘要 + 实体画像 + 长期记忆检索）
- inject_doc_node: 文档注入
- agent_node: LLM 调用（滑动窗口截断 + 记忆上下文注入）
- collect_retrieved_laws: 法条收集
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.prompts import (
    LEGAL_SYSTEM_PROMPT,
    LEGAL_SYSTEM_PROMPT_NO_TOOLS,
    MEMORY_PROFILE_TEMPLATE,
    MEMORY_LONGTERM_TEMPLATE,
    MEMORY_SUMMARY_TEMPLATE,
    SUPERVISOR_DIRECT_PROMPT,
    SUPERVISOR_FINAL_PROMPT,
    VIKING_CONTEXT_TEMPLATE,
)
from agent.state import AgentState
from agent.tools import ALL_TOOLS, LEGAL_CONSULT_TOOLS
from services.llm import get_llm, supports_tools
from services.memory import SLIDING_WINDOW_SIZE
from services.model_routing import select_model_route
from services.case_retrieval import format_cases_for_prompt, search_similar_cases
from services.legal_analysis import build_follow_up_response, score_legal_answer, should_ask_follow_up
from services.contract_agent.formatter import render_chat_summary
from services.contract_agent.schema import ContractReviewResult
from services.contract_report import save_contract_report
from services.supervisor import route_user_request_with_llm
from services.answer_format import strip_answer_markdown
from services.context_compaction import compact_state_context

log = logging.getLogger(__name__)
DOC_PREFIX = "[USER_DOCUMENT]"
MAX_TOOL_CALLS = int(os.getenv("MAX_TOOL_CALLS", "4"))


async def context_compaction_node(state: AgentState) -> dict[str, Any]:
    """
    函数作用：
        上下文压缩节点 —— 在读取记忆和路由前控制 LangGraph checkpoint 大小。
    输入参数：
        - state: AgentState
    输出参数：
        - dict[str, Any]
    """
    result = await compact_state_context(state)
    status = result.get("context_status")
    if status:
        _record_trace_event(
            state.get("trace_id"),
            "context_status",
            name="context_compaction",
            payload=status,
        )
    if result.get("context_compacted"):
        _record_trace_event(
            state.get("trace_id"),
            "context_compaction",
            name="context_compaction",
            payload=status or {},
        )
    return result


def _record_trace_event(trace_id: str | None, event_type: str, *, name: str = "", payload: dict[str, Any] | None = None) -> None:
    """
    函数作用：
        安全记录 Agent 事件，观测失败不影响主流程。
    输入参数：
        - trace_id: str | None
        - event_type: str
        - name: str，默认值 ''
        - payload: dict[str, Any] | None，默认值 None
    输出参数：
        - 无
    """
    if not trace_id:
        return
    try:
        from services.observability import record_event
        record_event(trace_id, event_type, name=name, payload=payload or {})
    except Exception as exc:
        log.debug("trace event skipped: %s", exc)


def memory_node(state: AgentState) -> dict[str, Any]:
    """
    函数作用：
        记忆加载节点 —— 分层输出三类记忆上下文。
    输入参数：
        - state: AgentState
    输出参数：
        - dict[str, Any]
    """
    thread_id = state.get("thread_id", "")
    if not thread_id:
        return {}

    from services.memory import get_summary, get_user_profile
    from services.memory_store import get_memory_store

    result: dict[str, Any] = {}

    # 实体记忆：用户画像
    profile = get_user_profile(thread_id)
    if profile:
        parts = []
        identity = profile.get("identity", "")
        focus = profile.get("focus_areas", [])
        if identity:
            parts.append(f"身份：{identity}")
        if focus:
            parts.append(f"关注领域：{'、'.join(focus)}")
        if parts:
            result["memory_profile"] = "\n".join(parts)

    # 短期记忆：历史摘要
    summary = get_summary(thread_id)
    if summary:
        result["memory_summary"] = summary

    # 长期记忆：语义检索相关记忆
    messages = state.get("messages", [])
    latest_query = ""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            latest_query = m.content
            break

    if latest_query:
        try:
            store = get_memory_store()
            relevant_memories = store.search_memories(latest_query, top_k=3)
            if relevant_memories:
                mem_lines = [f"- [{m.memory_type}] {m.content}" for m in relevant_memories]
                result["memory_longterm"] = "\n".join(mem_lines)
        except Exception as e:
            log.debug("长期记忆检索跳过: %s", e)

        try:
            from services.openviking_context import retrieve_agent_context
            viking_result = retrieve_agent_context(
                latest_query,
                thread_id=thread_id,
                profile=result.get("memory_profile"),
                summary=result.get("memory_summary"),
                longterm=result.get("memory_longterm"),
            )
            if viking_result.prompt:
                result["viking_context"] = viking_result.prompt
                result["viking_context_hits"] = [hit.to_dict() for hit in viking_result.hits]
                _record_trace_event(
                    state.get("trace_id"),
                    "viking_context_retrieval",
                    name="openviking_context_database",
                    payload={
                        "total": len(viking_result.hits),
                        "hits": result["viking_context_hits"],
                    },
                )
        except Exception as e:
            log.debug("OpenViking 风格上下文检索跳过: %s", e)

    return result


def inject_doc_node(state: AgentState) -> dict[str, Any]:
    """
    函数作用：
        文档注入节点 —— 若有上传文档且历史中没有对应 SystemMessage，则插入。
    输入参数：
        - state: AgentState
    输出参数：
        - dict[str, Any]
    """
    doc = state.get("uploaded_doc_text")
    evidence = state.get("uploaded_evidence_text")
    if not doc and not evidence:
        return {}
    messages = state.get("messages", [])
    additions: list[SystemMessage] = []

    has_doc_msg = any(
        isinstance(m, SystemMessage) and m.content.startswith(DOC_PREFIX)
        for m in messages
    )
    if doc and not has_doc_msg:
        name = state.get("uploaded_doc_name") or "未命名文档"
        additions.append(SystemMessage(content=f"{DOC_PREFIX} 文件名：{name}\n\n{doc}"))

    evidence_prefix = "[上传视频证据]"
    has_evidence_msg = any(
        isinstance(m, SystemMessage) and m.content.startswith(evidence_prefix)
        for m in messages
    )
    if evidence and not has_evidence_msg:
        additions.append(SystemMessage(content=f"{evidence_prefix}\n{evidence}"))

    return {"messages": additions} if additions else {}


def _latest_human_message(state: AgentState) -> str:
    """
    函数作用：
        从状态中获取最近一条用户消息。
    输入参数：
        - state: AgentState
    输出参数：
        - str
    """
    for item in reversed(state.get("messages", [])):
        if isinstance(item, HumanMessage):
            return item.content
    return ""


def _last_report_from(state: AgentState, agent_name: str) -> dict[str, Any] | None:
    """
    函数作用：
        从状态中取某个专家智能体的最后一份报告。
    输入参数：
        - state: AgentState
        - agent_name: str
    输出参数：
        - dict[str, Any] | None
    """
    for report in reversed(state.get("agent_reports", []) or []):
        if report.get("agent") == agent_name:
            return report
    return None


def _fallback_supervisor_final_response(state: AgentState) -> str:
    """
    函数作用：
        主控最终回复的兜底生成，避免 LLM 失败时无内容返回。
    输入参数：
        - state: AgentState
    输出参数：
        - str
    """
    reports = state.get("agent_reports", []) or []
    latest = reports[-1] if reports else {}
    for key in ("draft_response", "final_response", "analysis", "summary"):
        value = latest.get(key)
        if isinstance(value, str) and value.strip():
            return strip_answer_markdown(value)
    questions = latest.get("suggested_questions") or latest.get("questions") or []
    if questions:
        lines = "\n".join(f"{idx}. {question}" for idx, question in enumerate(questions[:3], start=1))
        return f"我还需要先确认几个关键信息：\n{lines}"
    return "我已经完成初步分析，但还需要你补充更多关键信息后，才能给出更稳妥的判断。"


def _next_route_after_agent_reports(state: AgentState) -> tuple[str, str]:
    """
    函数作用：
        主控根据专家报告决定继续调度还是最终输出。
    输入参数：
        - state: AgentState
    输出参数：
        - tuple[str, str]
    """
    latest = (state.get("agent_reports", []) or [])[-1]
    agent = latest.get("agent")
    status = latest.get("status")
    if (
        agent == "fact_agent"
        and status == "facts_sufficient"
        and _last_report_from(state, "legal_consult_agent") is None
    ):
        return "legal_consult_agent", "事实智能体确认事实足够，继续交给法律咨询智能体分析"
    return "end", f"{agent or '专家智能体'} 已返回报告，由主控生成最终回复"


async def _llm_supervisor_final_response(state: AgentState) -> str:
    """
    函数作用：
        主控智能体根据专家报告生成最终用户可见回答。
    输入参数：
        - state: AgentState
    输出参数：
        - str
    """
    latest_query = _latest_human_message(state)
    payload = {
        "用户问题": latest_query,
        "专家报告": state.get("agent_reports", []) or [],
        "检索法条": state.get("retrieved_laws", []) or [],
        "上传文档": state.get("uploaded_doc_name") or "",
    }
    fallback = _fallback_supervisor_final_response(state)
    try:
        llm = get_llm(
            provider=os.getenv("SUPERVISOR_PROVIDER", "zhipu"),
            model=os.getenv("SUPERVISOR_MODEL", "GLM-4.6V"),
            model_route="supervisor_agent",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0.2,
            streaming=False,
        )
        response = await llm.ainvoke([
            SystemMessage(content=SUPERVISOR_FINAL_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        content = strip_answer_markdown((response.content or "").strip()) or fallback
    except Exception as exc:
        _record_trace_event(
            state.get("trace_id"),
            "agent_fallback",
            name="supervisor_agent",
            payload={"error": str(exc)},
        )
        content = fallback

    retrieved = state.get("retrieved_laws", []) or []
    if retrieved:
        content = _guard_law_citations(content, retrieved)
        content = strip_answer_markdown(content)
    return content


async def _llm_supervisor_direct_response(state: AgentState, reason: str) -> str:
    """
    函数作用：
        主控智能体直接回复非法律/寒暄/情绪类输入。
    输入参数：
        - state: AgentState
        - reason: str
    输出参数：
        - str
    """
    latest_query = _latest_human_message(state)
    fallback = "我在，你慢慢说。可以先告诉我发生了什么，或者你现在最想解决哪件事。"
    payload = {
        "用户输入": latest_query,
        "路由理由": reason,
    }
    try:
        llm = get_llm(
            provider=os.getenv("SUPERVISOR_PROVIDER", "zhipu"),
            model=os.getenv("SUPERVISOR_MODEL", "GLM-4.6V"),
            model_route="supervisor_agent",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0.3,
            streaming=False,
        )
        response = await llm.ainvoke([
            SystemMessage(content=SUPERVISOR_DIRECT_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        return strip_answer_markdown((response.content or "").strip()) or fallback
    except Exception as exc:
        _record_trace_event(
            state.get("trace_id"),
            "agent_fallback",
            name="supervisor_agent",
            payload={"error": str(exc)},
        )
        return fallback


async def supervisor_agent_node(state: AgentState) -> dict[str, Any]:
    """
    函数作用：
        中控智能体节点 —— 根据用户意图路由到具体业务 Agent。
    输入参数：
        - state: AgentState
    输出参数：
        - dict[str, Any]
    """
    reports = state.get("agent_reports", []) or []
    if reports:
        route, reason = _next_route_after_agent_reports(state)
        if route != "end":
            _record_trace_event(
                state.get("trace_id"),
                "supervisor_route",
                name="supervisor_agent",
                payload={"route": route, "reason": reason, "from_reports": True},
            )
            return {
                "supervisor_route": route,
                "supervisor_reason": reason,
                "supervisor_finalized": False,
            }
        final_content = await _llm_supervisor_final_response(state)
        _record_trace_event(
            state.get("trace_id"),
            "final_answer",
            name="supervisor_agent",
            payload={
                "content_preview": final_content[:500],
                "answer_score": score_legal_answer(
                    _latest_human_message(state),
                    final_content,
                    state.get("retrieved_laws", []),
                ),
            },
        )
        return {
            "supervisor_route": "end",
            "supervisor_reason": reason,
            "supervisor_finalized": True,
            "messages": [AIMessage(content=final_content)],
        }

    latest_query = _latest_human_message(state)
    decision = await route_user_request_with_llm(
        message=latest_query,
        has_uploaded_doc=bool(state.get("uploaded_doc_text")),
        uploaded_doc_name=state.get("uploaded_doc_name"),
        trace_id=state.get("trace_id"),
        thread_id=state.get("thread_id"),
    )
    payload = {
        "route": decision.route,
        "reason": decision.reason,
        "complexity": decision.complexity,
        "need_tools": decision.need_tools,
    }
    _record_trace_event(
        state.get("trace_id"),
        "supervisor_route",
        name="supervisor_agent",
        payload=payload,
    )
    if decision.route == "final":
        final_content = await _llm_supervisor_direct_response(state, decision.reason)
        _record_trace_event(
            state.get("trace_id"),
            "final_answer",
            name="supervisor_agent",
            payload={
                "content_preview": final_content[:500],
                "answer_score": score_legal_answer(
                    latest_query,
                    final_content,
                    state.get("retrieved_laws", []),
                ),
            },
        )
        return {
            "supervisor_route": "end",
            "supervisor_reason": decision.reason,
            "supervisor_finalized": True,
            "messages": [AIMessage(content=final_content)],
        }
    return {
        "supervisor_route": decision.route,
        "supervisor_reason": decision.reason,
        "supervisor_finalized": False,
    }


def should_after_supervisor(state: AgentState) -> str:
    """
    函数作用：
        中控智能体后的条件边。
    输入参数：
        - state: AgentState
    输出参数：
        - str
    """
    route = state.get("supervisor_route") or "legal_consult_agent"
    if state.get("supervisor_finalized") or route in {"end", "final"}:
        return "end"
    if route in {"fact_agent", "contract_agent", "legal_consult_agent"}:
        return route
    return "legal_consult_agent"


async def _llm_fact_follow_up(
    state: AgentState,
    latest_query: str,
    decision: dict[str, Any],
) -> str:
    """
    函数作用：
        使用事实审查智能体生成追问话术，失败时返回规则模板。
    输入参数：
        - state: AgentState
        - latest_query: str
        - decision: dict[str, Any]
    输出参数：
        - str
    """
    fallback = build_follow_up_response(latest_query)
    try:
        prompt = (
            "你是事实审查智能体。用户的法律问题事实不足，请只追问 1-3 个关键事实，"
            "不要引用法条，不要下结论，语气简洁。"
        )
        payload = {
            "用户问题": latest_query,
            "缺失事实维度": decision.get("facts", {}).get("missing_dimensions", []),
            "建议问题": decision.get("questions", []),
        }
        llm = get_llm(
            provider=os.getenv("FACT_AGENT_PROVIDER", "zhipu"),
            model=os.getenv("FACT_AGENT_MODEL", "GLM-4.6V"),
            model_route="fact_agent",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0.2,
            streaming=False,
        )
        response = await llm.ainvoke([
            SystemMessage(content=prompt),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        return (response.content or "").strip() or fallback
    except Exception as exc:
        _record_trace_event(
            state.get("trace_id"),
            "agent_fallback",
            name="fact_agent",
            payload={"error": str(exc)},
        )
        return fallback


async def fact_agent_node(state: AgentState) -> dict[str, Any]:
    """
    函数作用：
        事实审查智能体 —— 对事实明显不足的法律问题先追问。
    输入参数：
        - state: AgentState
    输出参数：
        - dict[str, Any]
    """
    latest_query = _latest_human_message(state)
    if not latest_query:
        return {"needs_follow_up": False}
    decision = should_ask_follow_up(
        latest_query,
        has_uploaded_doc=bool(state.get("uploaded_doc_text")),
    )
    _record_trace_event(
        state.get("trace_id"),
        "fact_check",
        name="fact_agent",
        payload=decision,
    )
    if not decision["should_ask"]:
        return {
            "needs_follow_up": False,
            "agent_reports": [
                {
                    "agent": "fact_agent",
                    "status": "facts_sufficient",
                    "summary": decision.get("reason", "事实足够进入下一步分析"),
                    "missing_facts": [],
                    "suggested_questions": [],
                    "confidence": "medium",
                }
            ],
        }
    response = await _llm_fact_follow_up(state, latest_query, decision)
    return {
        "needs_follow_up": True,
        "agent_reports": [
            {
                "agent": "fact_agent",
                "status": "needs_more_facts",
                "summary": decision.get("reason", "事实不足，需要先追问"),
                "missing_facts": decision.get("facts", {}).get("missing_dimensions", []),
                "suggested_questions": decision.get("questions", []),
                "draft_response": response,
                "confidence": "medium",
            }
        ],
    }


async def fact_check_node(state: AgentState) -> dict[str, Any]:
    """
    函数作用：
        兼容旧测试和旧命名，实际委托给事实审查智能体。
    输入参数：
        - state: AgentState
    输出参数：
        - dict[str, Any]
    """
    return await fact_agent_node(state)


def should_after_fact_check(state: AgentState) -> str:
    """
    函数作用：
        事实检查后的条件边。
    输入参数：
        - state: AgentState
    输出参数：
        - str
    """
    return "end" if state.get("needs_follow_up") else "agent"


async def _llm_contract_summary(state: AgentState, markdown: str) -> str:
    """
    函数作用：
        使用合同审查智能体生成简短摘要，失败时返回固定提示。
    输入参数：
        - state: AgentState
        - markdown: str
    输出参数：
        - str
    """
    try:
        llm = get_llm(
            provider=os.getenv("CONTRACT_AGENT_PROVIDER", "zhipu"),
            model=os.getenv("CONTRACT_AGENT_MODEL", "glm-4.7"),
            model_route="contract_agent",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0.2,
            streaming=False,
        )
        response = await llm.ainvoke([
            SystemMessage(content="你是合同审查智能体。请根据报告生成 3 条以内的中文摘要，不要编造报告外内容。"),
            HumanMessage(content=markdown[:4000]),
        ])
        return (response.content or "").strip()
    except Exception as exc:
        _record_trace_event(
            state.get("trace_id"),
            "agent_fallback",
            name="contract_agent",
            payload={"error": str(exc)},
        )
        return ""


async def contract_agent_node(state: AgentState) -> dict[str, Any]:
    """
    函数作用：
        合同审查智能体 —— 基于上传文档生成合同审查报告入口。
    输入参数：
        - state: AgentState
    输出参数：
        - dict[str, Any]
    """
    doc_text = state.get("uploaded_doc_text")
    doc_name = state.get("uploaded_doc_name") or "合同文档"
    latest_query = _latest_human_message(state)
    if not doc_text:
        try:
            llm = get_llm(
                provider=os.getenv("CONTRACT_AGENT_PROVIDER", "zhipu"),
                model=os.getenv("CONTRACT_AGENT_MODEL", "glm-4.7"),
                model_route="contract_agent",
                trace_id=state.get("trace_id"),
                thread_id=state.get("thread_id"),
                temperature=0.2,
                streaming=False,
            )
            response = await llm.ainvoke([
                SystemMessage(content="你是合同审查智能体。用户想审查合同但没有上传文档，请提示上传合同并说明你会输出什么。"),
                HumanMessage(content=latest_query),
            ])
            content = (response.content or "").strip()
        except Exception:
            content = (
                "可以，我会按合同审查流程处理。请先上传合同文件，"
                "我会生成风险条款、修改建议、补充材料清单和 Markdown 审查报告。"
            )
        _record_trace_event(
            state.get("trace_id"),
            "contract_agent",
            name="missing_document",
            payload={"message": content},
        )
        return {
            "agent_reports": [
                {
                    "agent": "contract_agent",
                    "status": "missing_document",
                    "summary": "用户需要合同审查但尚未上传合同文档",
                    "draft_response": content,
                    "next_steps": ["上传合同文件"],
                    "confidence": "high",
                }
            ],
        }

    report = save_contract_report(doc_name, doc_text, latest_query)
    contract_result_data = report.get("contract_result") or {}
    contract_result = ContractReviewResult.model_validate(contract_result_data)
    summary = await _llm_contract_summary(state, report["markdown"])
    download_url = f"/api/reports/{report['report_id']}"
    content = render_chat_summary(
        contract_result,
        report_id=report["report_id"],
        download_url=download_url,
    )
    if summary:
        content += f"\n\n【合同审查摘要】\n{summary}"
    _record_trace_event(
        state.get("trace_id"),
            "contract_agent",
            name="contract_review_report",
            payload={
                "report_id": report["report_id"],
                "download_url": download_url,
                "contract_type": contract_result.contract_meta.contract_type,
                "overall_risk_level": contract_result.executive_summary.overall_risk_level,
                "preview": report["markdown"][:800],
            },
        )
    return {
        "agent_reports": [
            {
                "agent": "contract_agent",
                "status": "report_ready",
                "summary": summary or "合同审查报告已生成",
                "draft_response": content,
                "report_id": report["report_id"],
                "download_url": download_url,
                "contract_meta": contract_result.contract_meta.model_dump(mode="json"),
                "overall_risk_level": contract_result.executive_summary.overall_risk_level,
                "top_issues": [
                    issue.model_dump(mode="json")
                    for issue in contract_result.issues[:3]
                ],
                "contract_result": contract_result.model_dump(mode="json"),
                "preview": report["markdown"][:1200],
                "confidence": "high",
            }
        ],
    }


# ── 法条引用附加 ─────────────────────────────────────────────────────────────

_LAW_CITATION_RE = re.compile(
    r"《([^》]+)》\s*"
    r"(第[一二三四五六七八九十百千万亿零〇两\d]+条(?:之[一二三四五六七八九十百千万亿零〇两\d]+)?)"
)


def _normalize_law_name(name: str) -> str:
    """
    函数作用：
        规范化法名，用于比较模型引用与检索结果。
    输入参数：
        - name: str
    输出参数：
        - str
    """
    return re.sub(r"\s+", "", name).replace("中华人民共和国", "")


def _law_key(law_name: str, article_no: str) -> tuple[str, str]:
    """
    函数作用：
        生成法条匹配 key。
    输入参数：
        - law_name: str
        - article_no: str
    输出参数：
        - tuple[str, str]
    """
    return (_normalize_law_name(law_name), re.sub(r"\s+", "", article_no))


def _guard_law_citations(content: str, laws: list[dict]) -> str:
    """
    函数作用：
        移除未被本轮检索结果支撑的明确法条引用。
    输入参数：
        - content: str
        - laws: list[dict]
    输出参数：
        - str
    """
    if not content:
        return content

    allowed = {
        _law_key(item.get("law_name", ""), item.get("article_no", ""))
        for item in laws
        if item.get("law_name") and item.get("article_no")
    }
    # 没有检索结果时，删除所有法条引用（防止 LLM 瞎编）
    if not allowed:
        return _LAW_CITATION_RE.sub("", content)

    def replace_if_unverified(match: re.Match[str]) -> str:
        """
        函数作用：
            待补充。
        输入参数：
            - match: re.Match[str]
        输出参数：
            - str
        """
        law_name, article_no = match.group(1), match.group(2)
        if _law_key(law_name, article_no) in allowed:
            return match.group(0)
        return "（未在本轮检索结果中确认的法条引用已移除）"

    return _LAW_CITATION_RE.sub(replace_if_unverified, content)


def _format_law_sources(laws: list[dict]) -> str:
    """
    函数作用：
        将检索到的法条格式化为简洁的出处列表。
    输入参数：
        - laws: list[dict]
    输出参数：
        - str
    """
    if not laws:
        return ""
    # 去重
    seen: set[tuple[str, str]] = set()
    sources: list[str] = []
    for item in laws:
        law_name = item.get("law_name", "")
        article_no = item.get("article_no", "")
        key = (law_name, article_no)
        if key not in seen:
            seen.add(key)
            sources.append(f"《{law_name}》{article_no}")
    lines = "\n".join(sources)
    return f"\n\n---\n【引用法条】\n{lines}"


def _extract_json_object(content: str) -> dict[str, Any] | None:
    """
    函数作用：
        从模型输出中提取 JSON 对象。
    输入参数：
        - content: str
    输出参数：
        - dict[str, Any] | None
    """
    if not content:
        return None
    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def _law_basis_from_retrieval(laws: list[dict]) -> list[dict[str, str]]:
    """
    函数作用：
        将检索结果压缩为专家报告里的法条依据。
    输入参数：
        - laws: list[dict]
    输出参数：
        - list[dict[str, str]]
    """
    basis: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in laws:
        law_name = str(item.get("law_name") or "")
        article_no = str(item.get("article_no") or "")
        key = (law_name, article_no)
        if not law_name or not article_no or key in seen:
            continue
        seen.add(key)
        basis.append({
            "law_name": law_name,
            "article_no": article_no,
            "point": str(item.get("content") or "")[:160],
        })
    return basis


def _build_legal_agent_report(content: str, state: AgentState) -> dict[str, Any]:
    """
    函数作用：
        将法律咨询智能体的最终模型输出转换为内部专家报告。
    输入参数：
        - content: str
        - state: AgentState
    输出参数：
        - dict[str, Any]
    """
    retrieved = state.get("retrieved_laws", []) or []
    parsed = _extract_json_object(content)
    if parsed is None:
        analysis = strip_answer_markdown(content)
        if retrieved:
            analysis = strip_answer_markdown(_guard_law_citations(analysis, retrieved))
        report: dict[str, Any] = {
            "agent": "legal_consult_agent",
            "status": "analysis_ready",
            "legal_issues": [],
            "law_basis": _law_basis_from_retrieval(retrieved),
            "analysis": analysis,
            "risks": [],
            "next_steps": [],
            "raw_response": content,
            "confidence": "medium",
        }
    else:
        report = dict(parsed)
        report["agent"] = "legal_consult_agent"
        report.setdefault("status", "analysis_ready")
        report.setdefault("legal_issues", [])
        report.setdefault("law_basis", _law_basis_from_retrieval(retrieved))
        report.setdefault("risks", [])
        report.setdefault("next_steps", [])
        report.setdefault("confidence", "medium")
        if isinstance(report.get("analysis"), str):
            analysis = strip_answer_markdown(report["analysis"])
            if retrieved:
                analysis = strip_answer_markdown(_guard_law_citations(analysis, retrieved))
            report["analysis"] = analysis
        report["raw_response"] = content
    report["retrieved_law_count"] = len(retrieved)
    return report


def _limit_tool_calls(response: AIMessage, *, max_calls: int = 1) -> AIMessage:
    """
    函数作用：
        限制单轮专家智能体最多发起的工具调用数，降低并发工具风暴。
    输入参数：
        - response: AIMessage
        - max_calls: int，默认值 1
    输出参数：
        - AIMessage
    """
    calls = list(getattr(response, "tool_calls", None) or [])
    if len(calls) <= max_calls:
        return response
    try:
        response.tool_calls = calls[:max_calls]
    except Exception:
        return AIMessage(content=response.content or "", tool_calls=calls[:max_calls])
    return response


def _legal_consult_tools_for_state(state: AgentState) -> list[Any]:
    """
    函数作用：
        根据法律咨询智能体所处阶段选择可用工具。
    输入参数：
        - state: AgentState
    输出参数：
        - list[Any]
    """
    used_local_search = _has_used_legal_search_tool(state)
    if used_local_search:
        return [
            tool for tool in LEGAL_CONSULT_TOOLS
            if tool.name != "legal_search_tool"
        ]
    return [
        tool for tool in LEGAL_CONSULT_TOOLS
        if tool.name != "web_search_tool"
    ]


def _has_used_legal_search_tool(state: AgentState) -> bool:
    """
    函数作用：
        判断本轮是否已经执行过本地法条检索，避免其他工具误关闭 legal_search_tool。
    输入参数：
        - state: AgentState
    输出参数：
        - bool
    """
    if state.get("retrieved_laws"):
        return True
    for message in state.get("messages", []):
        if not isinstance(message, ToolMessage):
            continue
        if getattr(message, "name", None) == "legal_search_tool":
            return True
        try:
            payload = json.loads(message.content) if isinstance(message.content, str) else message.content
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if (
            "results" in payload
            and "status" in payload
            and ("score_threshold" in payload or "top_rerank_score" in payload)
        ):
            return True
    return False


async def legal_consult_agent_node(state: AgentState) -> dict[str, Any]:
    """
    函数作用：
        法律咨询智能体节点 —— 分层记忆注入 + RAG 工具循环 + LLM 调用。
    输入参数：
        - state: AgentState
    输出参数：
        - dict[str, Any]
    """
    base_prompt = LEGAL_SYSTEM_PROMPT if supports_tools() else LEGAL_SYSTEM_PROMPT_NO_TOOLS

    # 分层注入记忆上下文到系统提示词
    full_prompt = base_prompt

    profile = state.get("memory_profile", "")
    if profile:
        full_prompt += MEMORY_PROFILE_TEMPLATE.format(profile=profile)

    longterm = state.get("memory_longterm", "")
    if longterm:
        full_prompt += MEMORY_LONGTERM_TEMPLATE.format(longterm=longterm)

    summary = state.get("memory_summary", "")
    if summary:
        full_prompt += MEMORY_SUMMARY_TEMPLATE.format(summary=summary)

    viking_context = state.get("viking_context", "")
    if viking_context:
        full_prompt += VIKING_CONTEXT_TEMPLATE.format(context=viking_context)

    # 滑动窗口：只取最近 N 条消息，但保证不从 ToolMessage 开头（否则 API 报错）
    all_messages = list(state.get("messages", []))
    windowed_messages = all_messages[-SLIDING_WINDOW_SIZE:]
    while windowed_messages and isinstance(windowed_messages[0], ToolMessage):
        windowed_messages = windowed_messages[1:]

    latest_user = ""
    for item in reversed(windowed_messages):
        if isinstance(item, HumanMessage):
            latest_user = item.content
            break
    route = select_model_route(
        user_message=latest_user,
        doc_text=state.get("uploaded_doc_text"),
        tool_call_count=state.get("tool_call_count", 0),
    )
    similar_cases = search_similar_cases(latest_user)
    if similar_cases:
        full_prompt += format_cases_for_prompt(similar_cases)
        _record_trace_event(
            state.get("trace_id"),
            "case_retrieval",
            name="similar_scenarios",
            payload={"cases": similar_cases},
        )
    _record_trace_event(
        state.get("trace_id"),
        "model_route",
        name=route.name,
        payload={
            "route": route.name,
            "provider": route.provider,
            "model": route.model,
            "reason": route.reason,
            "complexity_score": route.complexity_score,
        },
    )

    llm = get_llm(
        provider=route.provider,
        model=route.model,
        model_route=route.name,
        trace_id=state.get("trace_id"),
        thread_id=state.get("thread_id"),
    )
    if supports_tools(route.provider):
        llm = llm.bind_tools(_legal_consult_tools_for_state(state))

    sys_prompt = SystemMessage(content=full_prompt)
    response = await llm.ainvoke([sys_prompt] + windowed_messages)

    # 有工具调用时递增计数
    if getattr(response, "tool_calls", None):
        response = _limit_tool_calls(response)
        result: dict[str, Any] = {"messages": [response]}
        result["tool_call_count"] = state.get("tool_call_count", 0) + 1
        _record_trace_event(
            state.get("trace_id"),
            "agent_tool_request",
            name="legal_consult_agent",
            payload={"tools": [tc.get("name", "") for tc in response.tool_calls]},
        )
        return result

    report = _build_legal_agent_report(response.content or "", state)
    _record_trace_event(
        state.get("trace_id"),
        "agent_report",
        name="legal_consult_agent",
        payload={
            "status": report.get("status"),
            "retrieved_law_count": report.get("retrieved_law_count", 0),
            "analysis_preview": str(report.get("analysis") or "")[:500],
        },
    )
    return {"agent_reports": [report]}


async def agent_node(state: AgentState) -> dict[str, Any]:
    """
    函数作用：
        兼容旧命名，实际委托给法律咨询智能体。
    输入参数：
        - state: AgentState
    输出参数：
        - dict[str, Any]
    """
    return await legal_consult_agent_node(state)


def should_continue(state: AgentState) -> str:
    """
    函数作用：
        条件边 —— 检查是否继续 ReAct 循环。
    输入参数：
        - state: AgentState
    输出参数：
        - str
    """
    messages = state.get("messages", [])
    if not messages:
        return "end"
    last = messages[-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        if state.get("tool_call_count", 0) >= MAX_TOOL_CALLS:
            log.warning("ReAct 循环达到上限 %d 次，强制结束", MAX_TOOL_CALLS)
            return "end"
        return "tools"
    return "end"


def collect_retrieved_laws(state: AgentState) -> dict[str, Any]:
    """
    函数作用：
        法条收集节点 —— 从所有 ToolMessage 中提取检索到的法条供前端展示。
    输入参数：
        - state: AgentState
    输出参数：
        - dict[str, Any]
    """
    messages = state.get("messages", [])
    all_laws: list[dict] = []
    seen_ids: set[str] = set()

    # 从所有 ToolMessage 中提取法条（去重）
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        try:
            payload = m.content
            if isinstance(payload, str):
                payload = json.loads(payload)

            items: list[dict] = []
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                if "results" in payload and isinstance(payload["results"], list):
                    items.extend(payload["results"])
                if "relevant_laws" in payload:
                    items.extend(payload["relevant_laws"])
                if "law_a" in payload and "law_b" in payload:
                    items.extend(payload["law_a"].get("articles", []))
                    items.extend(payload["law_b"].get("articles", []))

            for item in items:
                key = f"{item.get('law_name', '')}_{item.get('article_no', '')}"
                if key not in seen_ids:
                    seen_ids.add(key)
                    all_laws.append(item)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    if all_laws:
        _record_trace_event(
            state.get("trace_id"),
            "retrieval_collect",
            name="collect_laws",
            payload={"law_count": len(all_laws), "laws": all_laws[:10]},
        )
        return {"retrieved_laws": all_laws}
    return {}
