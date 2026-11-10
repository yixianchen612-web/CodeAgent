"""第 1 步产品化：DeepSeek 官方 API 客户端。

实现要点（调研 + 实测结论）:
* POST {base}/chat/completions，Authorization: Bearer <key>，OpenAI 兼容协议。
* deepseek-v4-pro 是推理型模型：返回 reasoning_content，思考消耗 max_tokens 配额。
* 模型默认开启 thinking；thinking 开启时 temperature 无效。
  因此调用方在工具调用阶段必须显式传 thinking={"type":"disabled"}。
* 网络错误 / 429 / 5xx 指数退避重试；401→AuthError；402→BalanceError。
* reasoning_effort 参数若被网关拒绝（400），自动去掉该参数重试一次。
* 仅用标准库 urllib，无第三方依赖（利于 PyInstaller 打包）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .base import LLMResponse
from .errors import AuthError, BalanceError, LLMError, RateLimitError

RETRYABLE_HTTP = {429, 500, 502, 503, 504}


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-v4-pro", timeout: int = 300,
                 max_retries: int = 2, retry_delay: float = 2.0):
        if not api_key:
            raise AuthError("未配置 API Key（请在 GUI 填写或设置环境变量 DEEPSEEK_API_KEY）")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    # ------------------------------------------------------------------ 传输层

    def _request(self, url: str, payload: dict) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _map_http_error(code: int, body: str) -> LLMError:
        msg = body[:300]
        if code == 401:
            return AuthError(f"API Key 无效或未授权 (401): {msg}")
        if code == 402:
            return BalanceError(f"账户余额不足 (402): {msg}")
        if code == 429:
            return RateLimitError(f"请求过于频繁 (429): {msg}")
        if code == 400:
            return LLMError(f"请求参数错误 (400): {msg}")
        return LLMError(f"HTTP {code}: {msg}")

    def _post_with_retry(self, payload: dict) -> dict:
        last: LLMError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._request(self.base_url + "/chat/completions", payload)
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                last = self._map_http_error(e.code, body)
                if e.code in RETRYABLE_HTTP and attempt < self.max_retries:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                raise last
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                raise LLMError(f"网络错误: {e}") from e
        raise last or LLMError("未知错误")

    # ------------------------------------------------------------------ 响应解析

    @staticmethod
    def _parse_response(data: dict) -> LLMResponse:
        choice = data["choices"][0]
        msg = choice.get("message") or {}
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"raw": fn.get("arguments") or ""}
            tool_calls.append({"id": tc.get("id") or "", "name": fn.get("name") or "",
                               "arguments": args})
        return LLMResponse(
            content=msg.get("content") or "",
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason") or "",
            usage=data.get("usage") or {},
        )

    # ------------------------------------------------------------------ 公开接口

    def complete(self, messages: list, tools: list | None = None,
                 temperature: float = 0.2, max_tokens: int = 16384,
                 thinking: dict | None = None, reasoning_effort: str | None = None,
                 response_format: dict | None = None) -> LLMResponse:
        payload: dict = {"model": self.model, "messages": messages,
                         "stream": False, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if thinking is not None:
            payload["thinking"] = thinking
        # thinking 开启时 temperature 无效，仅在关闭时传
        if thinking is None or thinking.get("type") == "disabled":
            payload["temperature"] = temperature
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        if response_format:
            payload["response_format"] = response_format
        try:
            data = self._post_with_retry(payload)
        except LLMError as e:
            # 网关不支持 reasoning_effort → 去掉后重试一次
            if reasoning_effort and "reasoning_effort" in str(e).lower():
                payload.pop("reasoning_effort", None)
                data = self._post_with_retry(payload)
            else:
                raise
        return self._parse_response(data)

    def list_models(self) -> list[str]:
        """预检：获取模型列表（同时验证 Key 有效性）。"""
        req = urllib.request.Request(
            self.base_url + "/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise self._map_http_error(e.code, e.read().decode("utf-8", "replace")) from e
        return [m.get("id", "") for m in data.get("data", [])]
