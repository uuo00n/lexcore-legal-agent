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

__all__ = [
    "CourtLevel",
    "DelilegalAuthenticationError",
    "DelilegalClient",
    "DelilegalConfigurationError",
    "DelilegalError",
    "DelilegalInvalidResponseError",
    "DelilegalSettings",
    "DelilegalTimeoutError",
    "DelilegalUpstreamError",
    "JudgementType",
    "SourceType",
]
