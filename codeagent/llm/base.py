"""LLM 客户端协议与响应模型。

设计（借鉴 claw-code 的 ApiClient trait）：运行循环只依赖这个最小协议，
真实 DeepSeek 客户端与离线 Mock 客户端互换即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LLMResponse:
    content: str = ""                       # 最终回答文本（不含 reasoning）
    tool_calls: list = field(default_factory=list)  # [{id, name, arguments(dict)}]
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)       # {prompt_tokens, completion_tokens}


class LLMClient(Protocol):
    """complete(messages, tools=None, **opts) -> LLMResponse

    opts 支持: temperature / max_tokens / thinking / reasoning_effort / response_format
    """

    def complete(self, messages: list, tools: list | None = None, **opts) -> LLMResponse:
        ...
