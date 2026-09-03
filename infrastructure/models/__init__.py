"""ORM 模型包。

导入本包即注册全部模型到 `Base.metadata`，Alembic 的 env.py 和测试建表都依赖
这一点，因此新增模型必须在此处导出。
"""
from __future__ import annotations

from infrastructure.models.agent_run import (
    AGENT_RUN_STATUSES,
    RUN_CANCELLED,
    RUN_ERROR,
    RUN_RUNNING,
    RUN_SUCCESS,
    TERMINAL_STATUSES,
    AgentRun,
)
from infrastructure.models.base import (
    Base,
    BigIntType,
    JSONBType,
    TimestampMixin,
    UUIDType,
    utcnow,
    uuid_pk,
)
from infrastructure.models.conversation import (
    CONVERSATION_STATUSES,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_DELETED,
    Conversation,
)
from infrastructure.models.message import (
    MESSAGE_ROLES,
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_TOOL,
    ROLE_USER,
    Message,
)
from infrastructure.models.operational import (
    AgentEvent,
    AgentTrace,
    ConversationSummary,
    Document,
    EvalRun,
    LlmCallLog,
    QuotaUsage,
    UserProfile,
)
from infrastructure.models.tool_call import ToolCall
from infrastructure.models.user import User

__all__ = [
    "AGENT_RUN_STATUSES",
    "AgentRun",
    "AgentEvent",
    "AgentTrace",
    "Base",
    "BigIntType",
    "CONVERSATION_STATUSES",
    "Conversation",
    "ConversationSummary",
    "Document",
    "EvalRun",
    "JSONBType",
    "MESSAGE_ROLES",
    "Message",
    "LlmCallLog",
    "QuotaUsage",
    "ROLE_ASSISTANT",
    "ROLE_SYSTEM",
    "ROLE_TOOL",
    "ROLE_USER",
    "RUN_CANCELLED",
    "RUN_ERROR",
    "RUN_RUNNING",
    "RUN_SUCCESS",
    "STATUS_ACTIVE",
    "STATUS_ARCHIVED",
    "STATUS_DELETED",
    "TERMINAL_STATUSES",
    "TimestampMixin",
    "ToolCall",
    "UUIDType",
    "User",
    "UserProfile",
    "utcnow",
    "uuid_pk",
]
