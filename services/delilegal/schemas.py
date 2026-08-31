"""得理请求、响应与统一来源模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from services.delilegal.enums import CourtLevel, JudgementType


class SourceMetadata(BaseModel):
    source_type: Literal["local_rag", "delilegal_law", "delilegal_case"]
    source_id: str
    title: str
    retrieved_at: str
    score: float | None = None


class LawSearchInput(BaseModel):
    query: str = Field(min_length=1)
    page_no: int = Field(default=1, ge=1)
    page_size: int = Field(default=5, ge=1, le=20)
    sort_field: Literal["correlation", "time"] = "correlation"
    sort_order: Literal["asc", "desc"] = "desc"


class LawSearchResult(BaseModel):
    """法规检索的内部统一模型，不暴露得理原始响应结构。"""

    id: str
    title: str
    law_name: str
    article: str | None = None
    content: str
    publish_date: str | None = None
    effective_date: str | None = None
    status: str | None = None
    source: str = "delilegal"
    score: float | None = None
    issued_no: str | None = None
    publisher_name: str | None = None
    active_date: str | None = None
    timeliness_name: str | None = None
    level_name: str | None = None
    highlights: list | None = None
    source_type: Literal["delilegal_law"] = "delilegal_law"


class LawSearchResponse(BaseModel):
    query_id: str | None = None
    total_count: int = 0
    total_page: int = 0
    items: list[LawSearchResult] = Field(default_factory=list)


class CaseSearchInput(BaseModel):
    page_no: int = Field(default=1, ge=1)
    page_size: int = Field(default=5, ge=1, le=20)
    sort_field: Literal["correlation", "time"] = "correlation"
    sort_order: Literal["asc", "desc"] = "desc"
    case_year_start: str | None = None
    case_year_end: str | None = None
    court_levels: list[CourtLevel] | None = None
    keywords: list[str] | None = None
    long_text: str | None = None
    judgement_types: list[JudgementType] | None = None

    @model_validator(mode="after")
    def normalize_search_mode(self) -> "CaseSearchInput":
        if self.long_text and self.long_text.strip():
            self.long_text = self.long_text.strip()
            self.keywords = None
        elif self.keywords:
            self.keywords = [item.strip() for item in self.keywords if item.strip()] or None
        if self.case_year_start and self.case_year_end:
            if self.case_year_start > self.case_year_end:
                raise ValueError("case_year_start must not be later than case_year_end")
        return self


class CaseSearchResult(BaseModel):
    """类案检索的内部统一模型，不暴露得理原始响应结构。"""

    id: str
    title: str
    court: str | None = None
    case_number: str | None = None
    case_date: str | None = None
    cause: str | None = None
    summary: str | None = None
    judgment: str
    source: str = "delilegal"
    score: float | None = None
    case_type: str | None = None
    judgement_type: str | None = None
    judgement_date: str | None = None
    level_of_trial: str | None = None
    publish_type: str | None = None
    publish_type_name: str | None = None
    content: str
    source_type: Literal["delilegal_case"] = "delilegal_case"


class CaseSearchResponse(BaseModel):
    query_id: str | None = None
    total_count: int = 0
    total_page: int = 0
    items: list[CaseSearchResult] = Field(default_factory=list)
