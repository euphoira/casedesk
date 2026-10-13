"""Small bounded Chat Completions adapter; model compatibility is user configured."""

import asyncio
import json
import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

MAX_REQUEST = 220_000
MAX_RESPONSE = 500_000


class ProviderError(RuntimeError):
    """Sanitized error suitable for the local UI."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Completion:
    calls: list[ToolCall]
    input_tokens: int = 0
    output_tokens: int = 0


def reject_constant(_value):
    raise ValueError("Non-finite JSON")


def decode(value):
    try:
        return json.loads(value, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ProviderError("模型返回了无效 JSON。") from None


class Provider:
    def __init__(
        self, base_url: str, api_key: str, model: str, *, transport=None, timeout=30, retries=2
    ):
        try:
            parsed = urlsplit(base_url)
            valid = (
                isinstance(base_url, str)
                and parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
            )
            _ = parsed.port
            httpx.URL(base_url)
        except (ValueError, TypeError, AttributeError, httpx.InvalidURL):
            valid = False
        if not valid:
            raise ProviderError("模型服务地址必须是有效的 HTTP(S) API 地址。")
        if not isinstance(api_key, str) or any(not 33 <= ord(c) <= 126 for c in api_key):
            raise ProviderError("模型密钥配置无效。")
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ProviderError("请配置支持 Chat Completions 工具调用的模型。")
        if type(retries) is not int or not 0 <= retries <= 3:
            raise ProviderError("重试次数必须为 0 至 3。")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 120
        ):
            raise ProviderError("模型超时必须为 0 至 120 秒。")
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.api_key, self.model = api_key, model
        self.transport, self.timeout, self.retries = transport, timeout, retries

    async def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        try:
            payload = json.dumps(
                {
                    "model": self.model,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "required",
                    "stream": False,
                },
                allow_nan=False,
            ).encode()
        except (ValueError, TypeError, RecursionError):
            raise ProviderError("模型请求不是有效 JSON。") from None
        if len(payload) > MAX_REQUEST or len(messages) > 40:
            raise ProviderError("模型上下文已超出大小上限。")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(
            timeout=self.timeout, transport=self.transport, follow_redirects=False
        ) as client:
            for attempt in range(self.retries + 1):
                failure = "模型服务暂不可用。"
                try:
                    async with asyncio.timeout(self.timeout):
                        async with client.stream(
                            "POST", self.endpoint, content=payload, headers=headers
                        ) as response:
                            if response.status_code == 429 or 500 <= response.status_code < 600:
                                failure = f"模型服务返回 HTTP {response.status_code}。"
                            elif response.status_code != 200:
                                raise ProviderError(f"模型服务返回 HTTP {response.status_code}。")
                            else:
                                body = bytearray()
                                async for chunk in response.aiter_bytes():
                                    if len(body) + len(chunk) > MAX_RESPONSE:
                                        raise ProviderError("模型响应超出大小上限。")
                                    body.extend(chunk)
                                return self.parse(bytes(body))
                except (httpx.TransportError, TimeoutError):
                    failure = "模型连接失败或超时。"
                except httpx.DecodingError:
                    raise ProviderError("模型响应编码无效。") from None
                if attempt == self.retries:
                    raise ProviderError(failure) from None
                await asyncio.sleep(min(0.25 * 2**attempt, 1.0))
        raise ProviderError("模型请求失败。")

    @staticmethod
    def parse(body: bytes) -> Completion:
        data = decode(body)
        try:
            choice = data["choices"][0]
            message = choice["message"]
            if choice.get("finish_reason") in {"length", "content_filter"} or message.get(
                "refusal"
            ):
                raise ProviderError("模型输出不完整或拒绝了该请求。")
            raw_calls = message["tool_calls"]
            if not isinstance(raw_calls, list) or not 1 <= len(raw_calls) <= 4:
                raise ProviderError("模型必须返回 1 至 4 个函数调用。")
            calls, identifiers = [], set()
            for item in raw_calls:
                identifier, function = item["id"], item["function"]
                if (
                    item["type"] != "function"
                    or not isinstance(identifier, str)
                    or not identifier
                    or len(identifier) > 100
                    or identifier in identifiers
                ):
                    raise ProviderError("模型工具调用标识无效。")
                name, arguments = function["name"], function["arguments"]
                if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,64}", name):
                    raise ProviderError("模型工具名无效。")
                if not isinstance(arguments, str) or len(arguments.encode("utf-8")) > 20_000:
                    raise ProviderError("模型工具参数无效或超限。")
                parsed = decode(arguments)
                if not isinstance(parsed, dict):
                    raise ProviderError("模型工具参数必须为 JSON 对象。")
                calls.append(ToolCall(identifier, name, parsed))
                identifiers.add(identifier)
            usage = data.get("usage")
            if usage is None:
                usage = {}
            if not isinstance(usage, dict):
                raise ProviderError("模型 token 用量无效。")
            counts = [usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)]
            if any(type(value) is not int or value < 0 for value in counts):
                raise ProviderError("模型 token 用量无效。")
            return Completion(calls, *counts)
        except (KeyError, TypeError, IndexError, AttributeError, UnicodeError):
            raise ProviderError("模型返回了无效工具调用协议。") from None
