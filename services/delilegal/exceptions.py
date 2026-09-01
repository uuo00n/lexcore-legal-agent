"""得理服务异常；兼容旧名字并归入统一 API 错误类型。"""
from services.errors import DelilegalAPIError


class DelilegalError(DelilegalAPIError):
    """所有得理服务异常的基类。"""


class DelilegalAuthenticationError(DelilegalError):
    """凭据缺失或上游拒绝认证。"""

    default_code = "authentication_failed"


class DelilegalTimeoutError(DelilegalError):
    """连接或读取超时。"""

    default_code = "upstream_timeout"
    default_retryable = True


class DelilegalUpstreamError(DelilegalError):
    """得理服务返回非认证类错误。"""

    default_code = "upstream_error"


class DelilegalInvalidResponseError(DelilegalError):
    """响应不是预期的 JSON/数据结构。"""

    default_code = "invalid_upstream_response"


class DelilegalConfigurationError(DelilegalError):
    """调用所需配置缺失或无效。"""

    default_code = "configuration_error"
