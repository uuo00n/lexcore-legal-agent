"""得理请求编码与内部来源类型。"""
from enum import Enum


class CourtLevel(str, Enum):
    """法院层级：0 最高、1 高级、2 中级、3 基层。"""

    SUPREME = "0"
    HIGH = "1"
    INTERMEDIATE = "2"
    BASIC = "3"


class JudgementType(str, Enum):
    """文书类型：判决、裁定、调解、决定、通知及其他。"""

    JUDGMENT = "30"
    RULING = "31"
    MEDIATION = "32"
    DECISION = "33"
    NOTICE = "34"
    OTHER = "99"


class SourceType(str, Enum):
    """当前阶段允许的法律事实来源。"""

    LOCAL_RAG = "local_rag"
    DELILEGAL_LAW = "delilegal_law"
    DELILEGAL_CASE = "delilegal_case"
