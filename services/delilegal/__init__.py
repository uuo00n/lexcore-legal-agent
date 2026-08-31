"""得理法律开放平台统一服务层。"""
from services.delilegal.client import DelilegalClient
from services.delilegal.config import DelilegalSettings
from services.delilegal.enums import CourtLevel, JudgementType, SourceType
from services.delilegal.exceptions import (
    DelilegalAuthenticationError,
    DelilegalConfigurationError,
    DelilegalError,
    DelilegalInvalidResponseError,
    DelilegalTimeoutError,
    DelilegalUpstreamError,
)
from services.delilegal.schemas import CaseSearchResult, LawSearchResult

__all__ = [
    "CourtLevel",
    "CaseSearchResult",
    "DelilegalAuthenticationError",
    "DelilegalClient",
    "DelilegalConfigurationError",
    "DelilegalError",
    "DelilegalInvalidResponseError",
    "DelilegalSettings",
    "DelilegalTimeoutError",
    "DelilegalUpstreamError",
    "JudgementType",
    "LawSearchResult",
    "SourceType",
]
