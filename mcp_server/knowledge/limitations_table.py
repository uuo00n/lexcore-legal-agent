"""兼容导入：诉讼时效规则已迁移到 Service Layer。"""
from services.limitations_rules import (
    DEFAULT_RULE,
    LIMITATION_RULES,
    SUSPENSION_WARNING,
    LimitationRule,
)


__all__ = [
    "DEFAULT_RULE",
    "LIMITATION_RULES",
    "SUSPENSION_WARNING",
    "LimitationRule",
]
