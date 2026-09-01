"""记忆提取器 —— 对话结束后异步提取并持久化记忆。

职责：
1. 摘要生成：当消息数超过滑动窗口时，压缩溢出部分为增量摘要
2. 长期记忆提取：从交互中提取独立记忆条目（语义/情节/程序），存入 ChromaDB
3. 实体记忆更新：从对话中提取用户画像变化

设计原则：
- 异步执行，不阻塞主对话流
- 提取失败时静默降级（记忆是增强功能，不影响核心对话）
- 存储粒度：一次完整交互 或 一个独立知识点
"""
from __future__ import annotations

import json
import inspect
import logging
import os
from typing import Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from services.llm import get_llm
from services.memory import (
    SLIDING_WINDOW_SIZE,
    MAX_WINDOW_TOKENS,
    estimate_tokens,
    get_summary,
    get_summary_msg_count,
    save_summary,
    save_user_profile,
    get_user_profile,
)
from services.memory_store import get_memory_store
from services.persistence import append_messages as save_messages
from services.persistence import load_messages as load_all_messages

log = logging.getLogger(__name__)
DEFAULT_MEMORY_LLM_MODEL = "deepseek-v4-flash"


# ─── 提取提示词 ──────────────────────────────────────────────────────────

_INCREMENTAL_SUMMARY_PROMPT = """你是一个对话摘要助手。请将以下新对话内容与已有摘要合并，生成一份更新后的摘要。

要求：
- 保留已有摘要中的关键信息
- 整合新对话的核心内容
- 摘要应简洁但信息完整，不超过 300 字
- 重点保留：用户的问题、得到的法律结论、涉及的法律名称

已有摘要：
{existing_summary}

新对话内容：
{new_messages}

更新后的摘要："""

_MEMORY_EXTRACT_PROMPT = """从以下对话中提取与当前用户长期交互有关、值得跨轮保留的信息。每条记忆应独立、简短。

只保留：用户明确陈述的稳定身份与偏好、持续关注领域、仍在推进的案件事实或用户要求记住的信息。
不要保存模型给出的通用法律知识、法条、临时工具结果、推测、敏感凭据或仅对当前一步有用的内容。

返回 JSON 数组，每个元素格式：
{{
  "content": "记忆内容（一句话描述）",
  "type": "semantic|episodic|procedural"
}}

类型说明：
- semantic（语义记忆）：用户稳定事实。例："用户是餐饮企业经营者"
- episodic（情节记忆）：仍可能影响后续对话的用户事件。例："用户正在处理未支付加班费的劳动争议"
- procedural（程序记忆）：用户行为模式/偏好。例："用户习惯先了解风险再问解决方案"

对话内容：
{conversation}

请只返回 JSON 数组（3-5条最重要的记忆），不要其他内容："""

_PROFILE_EXTRACT_PROMPT = """根据以下对话，提取或更新用户画像信息。返回 JSON：
{{
  "identity": "用户身份（企业主/员工/学生/律师等，未知则为空）",
  "focus_areas": ["关注的法律领域"],
  "preferences": ["交互偏好，如喜欢详细解释/喜欢简洁回答等"]
}}

已有画像（合并更新，不丢失已有信息）：
{existing_profile}

本轮对话：
{conversation}

请只返回 JSON："""


# ─── 工具函数 ──────────────────────────────────────────────────────────────

def _messages_to_dicts(messages: list[BaseMessage]) -> list[dict]:
    """
    函数作用：
        将 LangChain 消息对象转为可序列化的字典列表。
    输入参数：
        - messages: list[BaseMessage]
    输出参数：
        - list[dict]
    """
    result = []
    for m in messages:
        if isinstance(m, HumanMessage):
            result.append({"role": "human", "content": m.content})
        elif isinstance(m, AIMessage):
            if getattr(m, "tool_calls", None):
                continue
            content = m.content if isinstance(m.content, str) else str(m.content)
            if content.strip():
                result.append({"role": "ai", "content": content})
        elif isinstance(m, SystemMessage):
            if not m.content.startswith("[USER_DOCUMENT]"):
                result.append({"role": "system", "content": m.content})
    return result


def _format_messages_text(messages: list[dict], max_items: int = 12) -> str:
    """
    函数作用：
        将消息字典列表格式化为可读文本。
    输入参数：
        - messages: list[dict]
        - max_items: int，默认值 12
    输出参数：
        - str
    """
    recent = messages[-max_items:] if len(messages) > max_items else messages
    lines = []
    for m in recent:
        role_label = {"human": "用户", "ai": "助手", "system": "系统"}.get(m["role"], m["role"])
        content = m["content"][:500]
        lines.append(f"{role_label}：{content}")
    return "\n".join(lines)


def _user_messages(messages: list[dict]) -> list[dict]:
    """Long-term user memory must be derived from user statements, not model output."""
    return [item for item in messages if item.get("role") in {"human", "user"}]


def _safe_parse_json(text: str):
    """
    函数作用：
        安全解析 LLM 返回的 JSON（容忍 markdown 代码块包裹）。
    输入参数：
        - text: str
    输出参数：
        - 未标注
    """
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _get_memory_llm():
    """
    函数作用：
        获取后台记忆任务专用轻量模型，避免抢占主问答模型限额。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    return get_llm(
        model=os.getenv("MEMORY_EXTRACTOR_MODEL", DEFAULT_MEMORY_LLM_MODEL),
        model_route="memory_extractor",
        streaming=False,
    )


async def _maybe_await(value):
    """兼容生产异步存储函数与测试中注入的同步替身。"""
    return await value if inspect.isawaitable(value) else value


def _new_messages_for_archive(
    thread_id: str,
    msg_dicts: list[dict],
    *,
    archived: list[dict] | None = None,
) -> list[dict]:
    """
    函数作用：
        计算本轮相对持久归档新增的消息，避免重复保存已恢复的历史。
    输入参数：
        - thread_id: str
        - msg_dicts: list[dict]
    输出参数：
        - list[dict]
    """
    if archived is None:
        # 保留同步测试辅助入口；生产路径会显式传入异步加载后的归档。
        archived = load_all_messages(thread_id)
        if inspect.isawaitable(archived):
            raise RuntimeError("archived messages must be awaited before comparison")
    if not archived:
        return msg_dicts

    max_overlap = min(len(archived), len(msg_dicts))
    for overlap in range(max_overlap, 0, -1):
        if archived[-overlap:] == msg_dicts[:overlap]:
            return msg_dicts[overlap:]
    return msg_dicts


# ─── 主提取逻辑 ──────────────────────────────────────────────────────────

async def extract_and_save_memory(
    thread_id: str,
    messages: list[BaseMessage],
    *,
    user_id: str | None = None,
) -> None:
    """
    函数作用：
        对话结束后的记忆提取主入口（异步，失败静默）。
    输入参数：
        - thread_id: str
        - messages: list[BaseMessage]
        - user_id: str | None，长期记忆 namespace；缺省时按 thread 隔离
    输出参数：
        - 无
    """
    if not messages:
        return

    msg_dicts = _messages_to_dicts(messages)
    if not msg_dicts:
        return

    # 1. 消息归档
    try:
        archived = await _maybe_await(load_all_messages(thread_id))
        new_msg_dicts = _new_messages_for_archive(
            thread_id,
            msg_dicts,
            archived=archived,
        )
        if new_msg_dicts:
            await _maybe_await(save_messages(thread_id, new_msg_dicts))
        log.info("消息已归档: thread=%s, count=%d", thread_id, len(new_msg_dicts))
    except Exception as e:
        new_msg_dicts = msg_dicts
        log.warning("消息归档失败: %s", e)

    # OpenViking 风格案件工作区：将对话沉淀为 viking://memory/cases/{thread_id}
    try:
        from services.viking_context import save_case_workspace
        save_case_workspace(thread_id, msg_dicts)
    except Exception as e:
        log.warning("OpenViking 案件工作区写入失败: %s", e)

    # 2. 摘要更新（条数超过窗口 或 窗口内 token 超上限时触发）
    try:
        all_msgs = await _maybe_await(load_all_messages(thread_id))
        total_count = len(all_msgs)

        # 判断是否需要触发摘要
        need_summary = total_count > SLIDING_WINDOW_SIZE
        if not need_summary and total_count > 0:
            # 即使条数未超窗口，检查 token 上限
            window_msgs = all_msgs[-SLIDING_WINDOW_SIZE:]
            window_tokens = sum(estimate_tokens(m["content"]) for m in window_msgs)
            need_summary = window_tokens > MAX_WINDOW_TOKENS

        if need_summary:
            existing_summary = get_summary(thread_id) or "（暂无历史摘要）"
            summarized_count = get_summary_msg_count(thread_id)

            # 需要摘要的新溢出消息
            overflow_msgs = all_msgs[summarized_count:-SLIDING_WINDOW_SIZE]
            if overflow_msgs:
                overflow_text = _format_messages_text(overflow_msgs)
                llm = _get_memory_llm()
                summary_resp = await llm.ainvoke(
                    _INCREMENTAL_SUMMARY_PROMPT.format(
                        existing_summary=existing_summary,
                        new_messages=overflow_text,
                    )
                )
                new_summary = summary_resp.content.strip()
                if new_summary:
                    save_summary(thread_id, new_summary, total_count - SLIDING_WINDOW_SIZE)
                    log.info("历史摘要已更新: thread=%s", thread_id)
    except Exception as e:
        log.warning("摘要生成失败: %s", e)

    # 3. 长期记忆提取（存入 ChromaDB）
    try:
        conversation_text = _format_messages_text(_user_messages(new_msg_dicts or msg_dicts))
        if conversation_text:
            llm = _get_memory_llm()
            memory_resp = await llm.ainvoke(
                _MEMORY_EXTRACT_PROMPT.format(conversation=conversation_text)
            )
            memories = _safe_parse_json(memory_resp.content)

            if memories and isinstance(memories, list):
                store = get_memory_store()
                for mem in memories:
                    if isinstance(mem, dict) and "content" in mem and "type" in mem:
                        store.add_memory(
                            thread_id=thread_id,
                            content=mem["content"],
                            memory_type=mem["type"],
                            owner_id=user_id or thread_id,
                        )
                log.info("长期记忆已提取: thread=%s, count=%d", thread_id, len(memories))
    except Exception as e:
        log.warning("长期记忆提取失败: %s", e)

    # 4. 用户画像更新（实体记忆）
    try:
        existing_profile = get_user_profile(thread_id) or {}
        conversation_text = _format_messages_text(
            _user_messages(new_msg_dicts or msg_dicts),
            max_items=8,
        )
        llm = _get_memory_llm()
        profile_resp = await llm.ainvoke(
            _PROFILE_EXTRACT_PROMPT.format(
                existing_profile=json.dumps(existing_profile, ensure_ascii=False),
                conversation=conversation_text,
            )
        )
        profile_data = _safe_parse_json(profile_resp.content)

        if profile_data and isinstance(profile_data, dict):
            # 合并已有画像
            merged = {**existing_profile, **profile_data}
            if "focus_areas" in existing_profile and "focus_areas" in profile_data:
                merged["focus_areas"] = list(set(
                    existing_profile.get("focus_areas", [])
                    + profile_data.get("focus_areas", [])
                ))
            save_user_profile(thread_id, merged)
            log.info("用户画像已更新: thread=%s", thread_id)
    except Exception as e:
        log.warning("用户画像提取失败: %s", e)
