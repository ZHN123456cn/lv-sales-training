"""
test_llm.py — LLM 响应解析测试

不访问真实网络，仅测试 _BlockingLLMResponse / _StreamingLLMResponse
的解析和转换逻辑。

通过 conftest 中的辅助函数构造仿 OpenAI completion 对象。
"""

from __future__ import annotations

import json

import pytest

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.config import LLMConfig
from agent.llm import LLMClient, _BlockingLLMResponse, _StreamingLLMResponse, prepare_messages_for_provider
from tests.conftest import (
    make_blocking_completion,
    make_streaming_chunks,
    make_streaming_tool_call_chunks,
)


# ---------------------------------------------------------------------------
# _BlockingLLMResponse
# ---------------------------------------------------------------------------

class TestBlockingLLMResponse:
    def test_content(self):
        """非流式响应正确提取 content 文本。"""
        comp = make_blocking_completion("你好，世界！")
        resp = _BlockingLLMResponse(comp)
        assert resp.content == "你好，世界！"

    def test_no_tool_calls(self):
        """无 tool_calls 时 has_tool_calls 为 False，tool_calls 为空列表。"""
        comp = make_blocking_completion("direct answer")
        resp = _BlockingLLMResponse(comp)
        assert resp.has_tool_calls is False
        assert resp.tool_calls == []

    def test_tool_calls_parsed(self):
        """非流式响应正确解析 tool_calls，arguments 为已解析的 dict。"""
        tc = [{"id": "call_1", "name": "read_file", "arguments": '{"path": "test.txt"}'}]
        comp = make_blocking_completion("", tool_calls=tc)
        resp = _BlockingLLMResponse(comp)
        assert resp.has_tool_calls is True
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0]["name"] == "read_file"
        assert resp.tool_calls[0]["arguments"] == {"path": "test.txt"}
        assert resp.tool_calls[0]["id"] == "call_1"

    def test_tool_calls_invalid_json_defaults_to_empty_dict(self):
        """arguments JSON 解析失败时，arguments 降级为 {}，不抛异常。"""
        tc = [{"id": "call_x", "name": "shell", "arguments": "NOT_JSON"}]
        comp = make_blocking_completion("", tool_calls=tc)
        resp = _BlockingLLMResponse(comp)
        assert resp.tool_calls[0]["arguments"] == {}

    def test_to_message_no_tools(self):
        """to_message() 在无工具调用时返回纯文本 assistant 消息。"""
        comp = make_blocking_completion("hello")
        resp = _BlockingLLMResponse(comp)
        msg = resp.to_message()
        assert msg["role"] == "assistant"
        assert msg["content"] == "hello"
        assert "tool_calls" not in msg

    def test_to_message_with_tools(self):
        """to_message() 在有工具调用时包含 tool_calls 字段。"""
        tc = [{"id": "call_2", "name": "list_dir", "arguments": "{}"}]
        comp = make_blocking_completion("", tool_calls=tc)
        resp = _BlockingLLMResponse(comp)
        msg = resp.to_message()
        assert "tool_calls" in msg
        assert msg["tool_calls"][0]["function"]["name"] == "list_dir"

    def test_to_message_preserves_reasoning_content(self):
        """to_message() 保留 provider 返回的 reasoning_content，供 provider adapter 决定是否回传。"""
        comp = make_blocking_completion(
            "",
            tool_calls=[{"id": "call_r", "name": "skill_read", "arguments": '{"name":"weather"}'}],
            reasoning_content="模型内部思考状态",
        )
        resp = _BlockingLLMResponse(comp)
        msg = resp.to_message()
        assert msg["reasoning_content"] == "模型内部思考状态"

    def test_to_message_reads_reasoning_content_from_model_extra(self):
        """OpenAI SDK 可能把 provider 扩展字段放在 model_extra。"""
        comp = make_blocking_completion("answer")
        comp.choices[0].message.model_extra = {"reasoning_content": "extra reasoning"}
        resp = _BlockingLLMResponse(comp)

        msg = resp.to_message()

        assert msg["reasoning_content"] == "extra reasoning"

    def test_stream_text_yields_content(self):
        """stream_text() 在阻塞响应中 yield 完整内容字符串。"""
        comp = make_blocking_completion("complete text")
        resp = _BlockingLLMResponse(comp)
        tokens = list(resp.stream_text())
        assert "".join(tokens) == "complete text"


# ---------------------------------------------------------------------------
# _StreamingLLMResponse
# ---------------------------------------------------------------------------

class TestStreamingLLMResponse:
    def test_content_assembled(self):
        """流式响应的所有 chunk 正确拼接为完整 content。"""
        chunks = make_streaming_chunks("流式输出")
        resp = _StreamingLLMResponse(chunks)
        assert resp.content == "流式输出"

    def test_no_tool_calls(self):
        """纯文本流式响应 has_tool_calls 为 False。"""
        chunks = make_streaming_chunks("answer")
        resp = _StreamingLLMResponse(chunks)
        assert resp.has_tool_calls is False

    def test_tool_calls_assembled(self):
        """流式 tool_calls 各 chunk 正确合并为完整条目。"""
        tc_chunks = make_streaming_tool_call_chunks(
            tool_id="call_stream_1",
            tool_name="write_file",
            arguments='{"path": "out.txt", "content": "hi"}',
        )
        resp = _StreamingLLMResponse(tc_chunks)
        assert resp.has_tool_calls is True
        tc = resp.tool_calls[0]
        assert tc["name"] == "write_file"
        assert tc["arguments"]["path"] == "out.txt"
        assert tc["id"] == "call_stream_1"

    def test_stream_text_yields_per_chunk(self):
        """stream_text() 逐 chunk yield 文本（每个汉字一个 chunk）。"""
        chunks = make_streaming_chunks("ABC")
        resp = _StreamingLLMResponse(chunks)
        tokens = list(resp.stream_text())
        assert "".join(tokens) == "ABC"
        assert len(tokens) == 3   # 每个字符一个 chunk

    def test_to_message_no_tools(self):
        """流式纯文本响应 to_message() 返回正确的 assistant 消息。"""
        chunks = make_streaming_chunks("流式回答")
        resp = _StreamingLLMResponse(chunks)
        msg = resp.to_message()
        assert msg["role"] == "assistant"
        assert msg["content"] == "流式回答"
        assert "tool_calls" not in msg

    def test_to_message_with_tool_calls(self):
        """流式工具调用响应 to_message() 包含 tool_calls 字段。"""
        tc_chunks = make_streaming_tool_call_chunks(
            tool_id="call_s2",
            tool_name="search_files",
            arguments='{"pattern": "*.py"}',
        )
        resp = _StreamingLLMResponse(tc_chunks)
        msg = resp.to_message()
        assert "tool_calls" in msg
        assert msg["tool_calls"][0]["function"]["name"] == "search_files"


# ---------------------------------------------------------------------------
# Provider-aware message preparation
# ---------------------------------------------------------------------------

class TestPrepareMessagesForProvider:
    def test_deepseek_preserves_assistant_reasoning_content(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "天气"},
            {
                "role": "assistant",
                "reasoning_content": "需要调用 weather skill",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "skill_read", "arguments": '{"name":"weather"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "..."},
        ]

        prepared = prepare_messages_for_provider(messages, "deepseek")

        assert prepared[2]["reasoning_content"] == "需要调用 weather skill"
        assert "reasoning_content" not in prepared[0]
        assert "reasoning_content" not in prepared[1]
        assert "reasoning_content" not in prepared[3]

    def test_deepseek_drops_legacy_assistant_missing_reasoning_content(self):
        messages = [
            {"role": "user", "content": "first"},
            {
                "role": "assistant",
                "reasoning_content": "reason",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "skill_read", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "skill"},
            {"role": "assistant", "content": "legacy final without reasoning"},
            {"role": "user", "content": "follow up"},
        ]

        prepared = prepare_messages_for_provider(messages, "deepseek")

        assert {"role": "assistant", "content": "legacy final without reasoning"} not in prepared
        assert prepared[-1] == {"role": "user", "content": "follow up"}
        assert any(m.get("tool_call_id") == "c1" for m in prepared)

    def test_deepseek_drops_orphan_tool_when_legacy_tool_call_is_removed(self):
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "reasoning_content": "reason", "content": "ok"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "missing_reason",
                        "type": "function",
                        "function": {"name": "skill_read", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "missing_reason", "content": "orphan"},
            {"role": "user", "content": "follow up"},
        ]

        prepared = prepare_messages_for_provider(messages, "deepseek")

        assert not any(m.get("tool_call_id") == "missing_reason" for m in prepared)

    def test_non_deepseek_strips_reasoning_content(self):
        messages = [
            {
                "role": "assistant",
                "content": "answer",
                "reasoning_content": "provider-specific",
            }
        ]

        prepared = prepare_messages_for_provider(messages, "openai")

        assert prepared == [{"role": "assistant", "content": "answer"}]

    def test_prepare_messages_does_not_mutate_input(self):
        messages = [
            {
                "role": "assistant",
                "content": "answer",
                "reasoning_content": "provider-specific",
            }
        ]

        prepare_messages_for_provider(messages, "openai")

        assert messages[0]["reasoning_content"] == "provider-specific"


class TestLLMClientProviderPreparation:
    def test_chat_strips_reasoning_for_non_deepseek_provider(self):
        completion = make_blocking_completion("ok")
        create = MagicMock(return_value=completion)
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        cfg = LLMConfig(provider="openai", api_key="test", streaming=False)

        with patch("agent.llm.OpenAI", return_value=fake_client):
            client = LLMClient(cfg)
            client.chat([
                {"role": "assistant", "content": "x", "reasoning_content": "secret"}
            ])

        sent_messages = create.call_args.kwargs["messages"]
        assert sent_messages == [{"role": "assistant", "content": "x"}]

    def test_chat_preserves_reasoning_for_deepseek_provider(self):
        completion = make_blocking_completion("ok")
        create = MagicMock(return_value=completion)
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        cfg = LLMConfig(provider="deepseek", api_key="test", streaming=False)

        with patch("agent.llm.OpenAI", return_value=fake_client):
            client = LLMClient(cfg)
            client.chat([
                {"role": "assistant", "content": "x", "reasoning_content": "state"}
            ])

        sent_messages = create.call_args.kwargs["messages"]
        assert sent_messages == [
            {"role": "assistant", "content": "x", "reasoning_content": "state"}
        ]
