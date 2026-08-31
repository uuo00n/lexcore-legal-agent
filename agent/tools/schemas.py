"""Pydantic contracts exposed by the Agent retrieval tools."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.delilegal.enums import CourtLevel, JudgementType

MAX_TOOL_TOP_K = 5
MAX_TOOL_OUTPUT_CHARS = 12_000


class _ToolInput(BaseModel):
    """Shared strict input contract; trace_id is injected and hidden from the LLM."""

    model_config = ConfigDict(extra="forbid")

    trace_id: Annotated[str | None, InjectedState("trace_id")] = Field(
        default=None,
        exclude=True,
        description="由运行时注入的链路标识，模型无需也不得填写。",
    )


class LawSearchToolInput(_ToolInput):
    """Input contract for official statute retrieval."""

    query: str = Field(
        min_length=2,
        max_length=1_000,
        description="具体的法规、法条或司法解释检索问题，包含争议焦点关键词。",
    )
    top_k: int = Field(default=5, ge=1, le=MAX_TOOL_TOP_K)
    page_no: int = Field(default=1, ge=1, le=100)
    sort_field: Literal["correlation", "time"] = "correlation"
    sort_order: Literal["asc", "desc"] = "desc"

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("query must contain at least two non-whitespace characters")
        return value


class CaseSearchToolInput(_ToolInput):
    """Input contract for similar-case retrieval."""

    keywords: list[str] | None = Field(default=None, max_length=8)
    long_text: str | None = Field(
        default=None,
        max_length=6_000,
        description="复杂案情的精简事实、争议焦点和关键条件；有值时优先于 keywords。",
    )
    top_k: int = Field(default=5, ge=1, le=MAX_TOOL_TOP_K)
    page_no: int = Field(default=1, ge=1, le=100)
    sort_field: Literal["correlation", "time"] = "correlation"
    sort_order: Literal["asc", "desc"] = "desc"
    case_year_start: str | None = Field(default=None, pattern=r"^\d{4}$")
    case_year_end: str | None = Field(default=None, pattern=r"^\d{4}$")
    court_levels: list[CourtLevel] | None = Field(default=None, max_length=4)
    judgement_types: list[JudgementType] | None = Field(default=None, max_length=6)

    @model_validator(mode="after")
    def validate_search_mode(self) -> "CaseSearchToolInput":
        if self.long_text and self.long_text.strip():
            self.long_text = self.long_text.strip()
            self.keywords = None
        elif self.keywords:
            cleaned = [item.strip()[:100] for item in self.keywords if item.strip()]
            self.keywords = cleaned or None
        if not self.long_text and not self.keywords:
            raise ValueError("long_text or keywords is required")
        if self.case_year_start and self.case_year_end:
            if self.case_year_start > self.case_year_end:
                raise ValueError("case_year_start must not be later than case_year_end")
        return self


class RagSearchToolInput(_ToolInput):
    """Input contract for the indexed local statute corpus."""

    # Keep empty input parseable so the tool can return an Agent-readable Tool Error
    # instead of leaking a raw Pydantic validation exception through legacy callers.
    query: str = Field(max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=MAX_TOOL_TOP_K)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        return value.strip()


class ToolErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False


class RetrievalToolOutput(BaseModel):
    """Stable, context-bounded retrieval response returned to the Agent."""

    status: Literal["found", "no_relevant_result", "low_quality", "error"]
    source_type: Literal["local_rag", "delilegal_law", "delilegal_case"]
    trace_id: str
    latency_ms: float = Field(ge=0)
    success: bool
    evidence_insufficient: bool
    result_count: int = Field(default=0, ge=0, le=MAX_TOOL_TOP_K)
    total_count: int | None = Field(default=None, ge=0)
    query_id: str | None = None
    truncated: bool = False
    results: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_TOOL_TOP_K)
    error: ToolErrorDetail | None = None
    hint: str | None = None
    score_threshold: float | None = None
    top_rerank_score: float | None = None
