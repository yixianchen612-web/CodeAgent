"""LLM 相关错误类型。"""


class LLMError(Exception):
    """LLM 调用基础错误。"""


class AuthError(LLMError):
    """API Key 无效或未授权（HTTP 401）。"""


class BalanceError(LLMError):
    """账户余额不足（HTTP 402）。"""


class RateLimitError(LLMError):
    """请求限流（HTTP 429）。"""


class OutputParseError(LLMError):
    """模型输出无法按预期格式解析。"""
