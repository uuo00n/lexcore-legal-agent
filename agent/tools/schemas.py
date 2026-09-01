"""LangChain Tool Schema；业务字段复用 Service Layer 参数模型。"""
from __future__ import annotations

from typing import Annotated

from langgraph.prebuilt import InjectedState
from pydantic import ConfigDict, Field

from services.search import (
    CaseSearchParams,
    LawSearchParams,
    LocalLawSearchParams,
    SearchServiceResult,
    ToolErrorDetail,
)


MAX_TOOL_TOP_K = 5
MAX_TOOL_OUTPUT_CHARS = 12_000


class _InjectedTrace:
    """LangGraph 注入字段；不会出现在模型可见的 Tool Schema 中。"""

    model_config = ConfigDict(extra="forbid")
    trace_id: Annotated[str | None, InjectedState("trace_id")] = Field(
        default=None,
        exclude=True,
        description="由运行时注入的链路标识，模型无需也不得填写。",
    )


class LawSearchToolInput(LawSearchParams, _InjectedTrace):
    """正式法规检索 Tool Schema。"""


class CaseSearchToolInput(CaseSearchParams, _InjectedTrace):
    """类案检索 Tool Schema。"""


class RagSearchToolInput(LocalLawSearchParams, _InjectedTrace):
    """本地法库检索 Tool Schema。"""


class RetrievalToolOutput(SearchServiceResult):
    """兼容现有 Agent Tool 输出名称，字段由 Service Layer 统一定义。"""


__all__ = [
    "CaseSearchToolInput",
    "LawSearchToolInput",
    "MAX_TOOL_OUTPUT_CHARS",
    "MAX_TOOL_TOP_K",
    "RagSearchToolInput",
    "RetrievalToolOutput",
    "ToolErrorDetail",
]
