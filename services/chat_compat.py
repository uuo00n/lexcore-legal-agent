"""OpenAI 协议兼容层 —— 抹平「思考模式」实现与 langchain_openai 的差异。

langchain_openai.ChatOpenAI 只按官方 OpenAI 规范工作，与 DeepSeek 有两处硬冲突，
都表现为 HTTP 400 invalid_request_error：

1. `reasoning_content` 是非标字段，ChatOpenAI 既不解析也不回传（其 docstring 明确
   写了这点，并建议 DeepSeek 用户改用 ChatDeepSeek）。而 DeepSeek 思考模式要求带
   tool_calls 的 assistant 消息在下一轮把 `reasoning_content` 原样带回，否则报
   `The reasoning_content in the thinking mode must be passed back to the API.`
   —— 工具循环第一轮成功、第二轮必挂。
2. `with_structured_output()` 在 langchain-openai 1.x 默认 method="json_schema"，
   发的是 OpenAI Structured Outputs，DeepSeek 直接回
   `This response_format type is unavailable now`；退到 function_calling 又会撞上
   `Thinking mode does not support this tool_choice`，必须同时关掉思考模式。

因此本层做两件事：把 `reasoning_content` 抓进 additional_kwargs 并在下一轮回填；
把结构化输出切到 function_calling 且该次调用关闭思考。抓取对任何 provider 都无害，
回填只在真的抓到过时发生，所以不会给不认识该字段的 provider 凭空加参数。
"""
from __future__ import annotations

from typing import Any, Literal

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI


REASONING_KEY = "reasoning_content"
# 关思考的开关。DeepSeek 同时接受 reasoning_effort="none" 与
# extra_body={"thinking": {"type": "disabled"}}，前者是 ChatOpenAI 的原生字段，优先。
NO_THINKING_EFFORT = "none"
# 思考模式不接受强制 tool_choice，结构化输出只能走「关思考 + function_calling」。
COMPAT_STRUCTURED_METHOD = "function_calling"


def _choice_reasonings(response: Any) -> list[str | None]:
    """按 choice 顺序取出 reasoning_content，dict 与 openai 响应对象都支持。"""
    if isinstance(response, dict):
        return [
            (choice.get("message") or {}).get(REASONING_KEY)
            for choice in response.get("choices") or []
            if isinstance(choice, dict)
        ]
    reasonings: list[str | None] = []
    for choice in getattr(response, "choices", None) or []:
        message = getattr(choice, "message", None)
        reasonings.append(None if message is None else getattr(message, REASONING_KEY, None))
    return reasonings


class CompatChatOpenAI(ChatOpenAI):
    """兼容思考模式 Provider 的 ChatOpenAI。

    `thinking_compat=True` 时额外接管结构化输出；`reasoning_content` 的抓取与回填
    始终生效，与该开关无关。
    """

    thinking_compat: bool = False

    def _create_chat_result(
        self,
        response: Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        """把 provider 返回的 reasoning_content 存进 AIMessage.additional_kwargs。"""
        result = super()._create_chat_result(response, generation_info)
        for generation, reasoning in zip(result.generations, _choice_reasonings(response)):
            message = getattr(generation, "message", None)
            if reasoning and isinstance(message, AIMessage):
                message.additional_kwargs[REASONING_KEY] = reasoning
        return result

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """回填 reasoning_content：assistant 消息与 AIMessage 顺序一一对应。"""
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        raw_messages = payload.get("messages")
        if not isinstance(raw_messages, list):
            # Responses API 用的是 payload["input"]，不走这条路径。
            return payload
        reasonings = [
            message.additional_kwargs.get(REASONING_KEY)
            for message in self._convert_input(input_).to_messages()
            if isinstance(message, AIMessage)
        ]
        cursor = 0
        for raw in raw_messages:
            if not isinstance(raw, dict) or raw.get("role") != "assistant":
                continue
            if cursor >= len(reasonings):
                break
            reasoning = reasonings[cursor]
            cursor += 1
            if reasoning and REASONING_KEY not in raw:
                raw[REASONING_KEY] = reasoning
        return payload

    def with_structured_output(
        self,
        schema: Any = None,
        *,
        method: Literal["function_calling", "json_mode", "json_schema"] = "json_schema",
        include_raw: bool = False,
        strict: bool | None = None,
        tools: list | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, Any]:
        """默认的 json_schema 在思考模式 Provider 上不可用，换成关思考 + 强制工具调用。

        只改「调用方没有显式指定 method」这一种情况：显式传入的 method 一律尊重。
        """
        target: ChatOpenAI = self
        if self.thinking_compat and method == "json_schema":
            method = COMPAT_STRUCTURED_METHOD
            if self.reasoning_effort != NO_THINKING_EFFORT:
                target = self.model_copy(update={"reasoning_effort": NO_THINKING_EFFORT})
        return ChatOpenAI.with_structured_output(
            target,
            schema,
            method=method,
            include_raw=include_raw,
            strict=strict,
            tools=tools,
            **kwargs,
        )
