"""得理服务统一异常；错误文本不得包含凭据。"""


class DelilegalError(Exception):
    """所有得理服务异常的基类。"""


class DelilegalAuthenticationError(DelilegalError):
    """凭据缺失或上游拒绝认证。"""


class DelilegalTimeoutError(DelilegalError):
    """连接或读取超时。"""


class DelilegalUpstreamError(DelilegalError):
    """得理服务返回非认证类错误。"""


class DelilegalInvalidResponseError(DelilegalError):
    """响应不是预期的 JSON/数据结构。"""


class DelilegalConfigurationError(DelilegalError):
    """调用所需配置缺失或无效。"""
