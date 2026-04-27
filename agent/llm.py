"""
LLM 客户端封装。

使用 OpenAI SDK 连接 DeepSeek / OpenAI 兼容接口，支持：
- Function Calling（工具调用）
- 流式输出
- 非流式输出
"""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

from openai import OpenAI

from .config import LLMConfig


class LLMClient:
    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool | None = None,
    ) -> "LLMResponse":
        """
        发起一次对话请求。

        Args:
            messages: OpenAI 格式消息列表
            tools:    工具 schema 列表（OpenAI function calling 格式）
            stream:   是否流式；None 则使用 config.streaming

        Returns:
            LLMResponse 对象，可判断是否含 tool_calls，也可迭代流式 token
        """
        use_stream = self._config.streaming if stream is None else stream

        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "temperature": self._config.temperature,
            "stream": use_stream,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        raw = self._client.chat.completions.create(**kwargs)

        if use_stream:
            return _StreamingLLMResponse(raw)
        else:
            return _BlockingLLMResponse(raw)

    def chat_blocking(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> "LLMResponse":
        """强制非流式调用（loop 内部使用，工具调用阶段不适合流式）。"""
        return self.chat(messages, tools, stream=False)


# ---------------------------------------------------------------------------
# 响应抽象
# ---------------------------------------------------------------------------

class LLMResponse:
    """统一的 LLM 响应接口。"""

    @property
    def content(self) -> str:
        """最终文本内容（阻塞直到完整）。"""
        raise NotImplementedError

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        """
        返回工具调用列表，格式：
        [
          {
            "id": "call_xxx",
            "name": "tool_name",
            "arguments": {...}   # 已解析的 dict
          }
        ]
        空列表表示无工具调用。
        """
        raise NotImplementedError

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    def stream_text(self) -> Generator[str, None, None]:
        """逐 token yield 文本（仅在无工具调用时有意义）。"""
        raise NotImplementedError

    def to_message(self) -> dict[str, Any]:
        """将响应转为 messages 格式的 assistant 消息 dict。"""
        raise NotImplementedError


class _BlockingLLMResponse(LLMResponse):
    def __init__(self, completion: Any) -> None:
        self._msg = completion.choices[0].message

    @property
    def content(self) -> str:
        return self._msg.content or ""

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        if not self._msg.tool_calls:
            return []
        result = []
        for tc in self._msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            result.append({
                "id": tc.id,
                "name": tc.function.name,
                "arguments": args,
            })
        return result

    def stream_text(self) -> Generator[str, None, None]:
        yield self.content

    def to_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant"}
        if self._msg.content:
            msg["content"] = self._msg.content
        if self._msg.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in self._msg.tool_calls
            ]
        return msg


class _StreamingLLMResponse(LLMResponse):
    """
    流式响应：先收集完整内容，再暴露给调用方。
    工具调用阶段会自动降级为阻塞（因为流式 tool_calls 需要拼接）。
    """

    def __init__(self, stream: Any) -> None:
        self._chunks: list[Any] = list(stream)
        self._content, self._raw_tool_calls = self._parse_chunks()

    def _parse_chunks(self) -> tuple[str, list[dict[str, Any]]]:
        content_parts: list[str] = []
        # tool_calls 是按 index 拆开流式发送的，需要合并
        tc_map: dict[int, dict[str, Any]] = {}

        for chunk in self._chunks:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_map:
                        tc_map[idx] = {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc_delta.id:
                        tc_map[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc_map[idx]["function"]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc_map[idx]["function"]["arguments"] += tc_delta.function.arguments

        raw_tool_calls = [tc_map[i] for i in sorted(tc_map)]
        return "".join(content_parts), raw_tool_calls

    @property
    def content(self) -> str:
        return self._content

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        result = []
        for tc in self._raw_tool_calls:
            try:
                args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}
            result.append({
                "id": tc["id"],
                "name": tc["function"]["name"],
                "arguments": args,
            })
        return result

    def stream_text(self) -> Generator[str, None, None]:
        """重放已收集的内容（逐 chunk yield）。"""
        for chunk in self._chunks:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    def to_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant"}
        if self._content:
            msg["content"] = self._content
        if self._raw_tool_calls:
            msg["tool_calls"] = self._raw_tool_calls
        return msg
