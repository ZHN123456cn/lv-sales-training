"""
test_loop.py — Agent Loop 集成测试

使用 Mock LLM，不调用真实 API。
覆盖 agent/loop.py 的核心逻辑：
- 直接回答（无工具）
- 单次 / 多次工具调用
- 最大迭代限制
- 消息序列正确性
- 回调触发
- 流式输出
- change_dir 影响 tool_ctx
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.config import AgentConfig
from agent.llm import LLMClient, _BlockingLLMResponse, _StreamingLLMResponse
from agent.loop import AgentLoop, ToolCallEvent, ToolResultEvent
from agent.session import Session
from agent.tools import ToolContext
from tests.conftest import (
    make_blocking_completion,
    make_streaming_chunks,
    make_streaming_tool_call_chunks,
)


# ---------------------------------------------------------------------------
# 辅助：构造 Mock LLM
# ---------------------------------------------------------------------------

def make_mock_llm(*responses) -> MagicMock:
    """
    构造 Mock LLMClient，chat_blocking 按顺序返回 responses 中的 LLMResponse。
    responses 中的每个元素应该是一个 LLMResponse 实例。
    """
    mock = MagicMock(spec=LLMClient)
    mock.chat_blocking.side_effect = list(responses)
    mock.chat.side_effect = list(responses)
    return mock


def blocking_resp(content: str, tool_calls=None) -> _BlockingLLMResponse:
    return _BlockingLLMResponse(make_blocking_completion(content, tool_calls))


def streaming_resp(content: str) -> _StreamingLLMResponse:
    return _StreamingLLMResponse(make_streaming_chunks(content))


def tool_call_resp(tool_id: str, tool_name: str, args_json: str) -> _BlockingLLMResponse:
    tc = [{"id": tool_id, "name": tool_name, "arguments": args_json}]
    return _BlockingLLMResponse(make_blocking_completion("", tool_calls=tc))


# ---------------------------------------------------------------------------
# 辅助：构造 AgentLoop
# ---------------------------------------------------------------------------

def make_loop(
    config: AgentConfig,
    session: Session,
    llm: LLMClient,
    skill_registry=None,
) -> AgentLoop:
    return AgentLoop(config=config, session=session, llm=llm, skill_registry=skill_registry)


# ---------------------------------------------------------------------------
# 基础：直接回答
# ---------------------------------------------------------------------------

class TestDirectAnswer:
    def test_direct_answer_no_tools(self, default_config: AgentConfig, empty_session: Session):
        """LLM 直接回答时，不调用任何工具，返回文本。"""
        llm = make_mock_llm(blocking_resp("直接回答"))
        loop = make_loop(default_config, empty_session, llm)
        result = loop.run("你好")
        assert result == "直接回答"

    def test_system_prompt_sent_to_llm_but_not_persisted(
        self, default_config: AgentConfig, empty_session: Session
    ):
        """system prompt 由 ContextManager 发给 LLM，但不写入 session。"""
        llm = make_mock_llm(blocking_resp("ok"))
        loop = make_loop(default_config, empty_session, llm)
        loop.run("测试")
        sent_messages = llm.chat_blocking.call_args.kwargs["messages"]
        assert sent_messages[0]["role"] == "system"
        assert all(m["role"] != "system" for m in empty_session.messages)

    def test_system_prompt_not_duplicated(
        self, default_config: AgentConfig, empty_session: Session
    ):
        """多次 run() 不会把 system 消息写入 session。"""
        llm = make_mock_llm(blocking_resp("r1"), blocking_resp("r2"))
        loop = make_loop(default_config, empty_session, llm)
        loop.run("first")
        loop.run("second")
        system_msgs = [m for m in empty_session.messages if m["role"] == "system"]
        assert len(system_msgs) == 0

    def test_user_message_appended(self, default_config: AgentConfig, empty_session: Session):
        """run() 后 messages 中包含对应的 user 消息。"""
        llm = make_mock_llm(blocking_resp("answer"))
        loop = make_loop(default_config, empty_session, llm)
        loop.run("用户的问题")
        user_msgs = [m for m in empty_session.messages if m["role"] == "user"]
        assert any("用户的问题" in m["content"] for m in user_msgs)


# ---------------------------------------------------------------------------
# 单次工具调用
# ---------------------------------------------------------------------------

class TestSingleToolCall:
    def test_tool_executed_and_result_in_messages(
        self, default_config: AgentConfig, empty_session: Session, tmp_work_dir: Path
    ):
        """LLM 返回工具调用 → 工具执行 → 工具结果追加 → LLM 再次调用 → 最终回答。"""
        # 第一轮：调用 list_dir
        # 第二轮：直接回答
        r1 = tool_call_resp("c1", "list_dir", "{}")
        r2 = blocking_resp("目录列出完毕")
        llm = make_mock_llm(r1, r2)
        loop = make_loop(default_config, empty_session, llm)
        result = loop.run("列出目录")
        assert result == "目录列出完毕"

        # messages 序列：user / assistant(tool_call) / tool / assistant(final)
        roles = [m["role"] for m in empty_session.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]

    def test_tool_result_appended_as_tool_role(
        self, default_config: AgentConfig, empty_session: Session
    ):
        """工具结果以 role=tool 追加到 messages。"""
        r1 = tool_call_resp("c2", "list_dir", "{}")
        r2 = blocking_resp("done")
        llm = make_mock_llm(r1, r2)
        loop = make_loop(default_config, empty_session, llm)
        loop.run("test")
        tool_msgs = [m for m in empty_session.messages if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "tool_call_id" in tool_msgs[0]


# ---------------------------------------------------------------------------
# 多次工具调用
# ---------------------------------------------------------------------------

class TestMultipleToolCalls:
    def test_multi_iterations(
        self, default_config: AgentConfig, empty_session: Session
    ):
        """需要多轮工具调用才能得到最终回答。"""
        r1 = tool_call_resp("c3", "list_dir", "{}")
        r2 = tool_call_resp("c4", "list_dir", "{}")
        r3 = blocking_resp("最终答案")
        llm = make_mock_llm(r1, r2, r3)
        loop = make_loop(default_config, empty_session, llm)
        result = loop.run("多轮任务")
        assert result == "最终答案"
        # 两轮工具调用 → 两个 tool 消息
        tool_msgs = [m for m in empty_session.messages if m["role"] == "tool"]
        assert len(tool_msgs) == 2


# ---------------------------------------------------------------------------
# Skill 读取
# ---------------------------------------------------------------------------

class TestSkillRead:
    def test_skill_read_then_final_answer(
        self, default_config: AgentConfig, empty_session: Session, skill_registry
    ):
        """LLM 可先调用 skill_read，读取 SKILL.md 后再给最终回答。"""
        r1 = tool_call_resp("sk1", "skill_read", '{"name": "research"}')
        r2 = blocking_resp("已按 research skill 完成")
        llm = make_mock_llm(r1, r2)
        loop = make_loop(default_config, empty_session, llm, skill_registry=skill_registry)

        result = loop.run("做研究")

        assert result == "已按 research skill 完成"
        tool_msgs = [m for m in empty_session.messages if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "[Skill] research" in tool_msgs[0]["content"]
        assert "[BaseDir]" in tool_msgs[0]["content"]

    def test_context_manager_used_for_llm_messages(
        self, default_config: AgentConfig, empty_session: Session, skill_registry
    ):
        """LLM 收到的 messages 包含 ContextManager 注入的 Skill 摘要。"""
        llm = make_mock_llm(blocking_resp("ok"))
        loop = make_loop(default_config, empty_session, llm, skill_registry=skill_registry)

        loop.run("hello")

        sent_messages = llm.chat_blocking.call_args.kwargs["messages"]
        assert "<available_skills>" in sent_messages[0]["content"]
        assert "<name>research</name>" in sent_messages[0]["content"]

    def test_multiple_tool_calls_in_one_response(
        self, default_config: AgentConfig, empty_session: Session
    ):
        """单次响应包含多个 tool_calls，全部执行后结果都追加到 messages。"""
        tc = [
            {"id": "ca", "name": "list_dir", "arguments": "{}"},
            {"id": "cb", "name": "list_dir", "arguments": "{}"},
        ]
        comp = make_blocking_completion("", tool_calls=tc)
        r1 = _BlockingLLMResponse(comp)
        r2 = blocking_resp("两个工具都调用了")
        llm = make_mock_llm(r1, r2)
        loop = make_loop(default_config, empty_session, llm)
        loop.run("parallel tools")
        tool_msgs = [m for m in empty_session.messages if m["role"] == "tool"]
        assert len(tool_msgs) == 2


# ---------------------------------------------------------------------------
# 最大迭代
# ---------------------------------------------------------------------------

class TestMaxIterations:
    def test_max_iterations_returns_warning(
        self, default_config: AgentConfig, empty_session: Session
    ):
        """超过 max_iterations 时返回警告字符串而非崩溃。"""
        default_config.max_iterations = 2
        # 每次都返回工具调用，永远不给出最终回答
        responses = [tool_call_resp(f"c{i}", "list_dir", "{}") for i in range(10)]
        llm = make_mock_llm(*responses)
        loop = make_loop(default_config, empty_session, llm)
        result = loop.run("无限工具")
        assert "警告" in result or "迭代" in result


# ---------------------------------------------------------------------------
# 工具执行错误
# ---------------------------------------------------------------------------

class TestToolError:
    def test_tool_error_does_not_stop_loop(
        self, default_config: AgentConfig, empty_session: Session
    ):
        """工具执行出错时（返回 [错误] 字符串），loop 继续而非崩溃，最终仍能返回答案。"""
        # 调用一个不存在的工具
        r1 = tool_call_resp("ce", "nonexistent_tool_xyz", "{}")
        r2 = blocking_resp("错误已处理，继续回答")
        llm = make_mock_llm(r1, r2)
        loop = make_loop(default_config, empty_session, llm)
        result = loop.run("call bad tool")
        assert result == "错误已处理，继续回答"

        # tool 消息中包含错误描述
        tool_msgs = [m for m in empty_session.messages if m["role"] == "tool"]
        assert "[错误]" in tool_msgs[0]["content"]


# ---------------------------------------------------------------------------
# 回调
# ---------------------------------------------------------------------------

class TestCallbacks:
    def test_on_tool_call_callback_fired(
        self, default_config: AgentConfig, empty_session: Session
    ):
        """on_tool_call 回调在工具调用前被触发，携带正确事件信息。"""
        r1 = tool_call_resp("ck1", "list_dir", '{"path": "."}')
        r2 = blocking_resp("done")
        llm = make_mock_llm(r1, r2)
        loop = make_loop(default_config, empty_session, llm)

        events: list[ToolCallEvent] = []
        loop.run("test", on_tool_call=events.append)

        assert len(events) == 1
        assert events[0].name == "list_dir"
        assert events[0].call_id == "ck1"
        assert events[0].arguments == {"path": "."}

    def test_on_tool_result_callback_fired(
        self, default_config: AgentConfig, empty_session: Session
    ):
        """on_tool_result 回调在工具执行后被触发，携带结果。"""
        r1 = tool_call_resp("ck2", "list_dir", "{}")
        r2 = blocking_resp("done")
        llm = make_mock_llm(r1, r2)
        loop = make_loop(default_config, empty_session, llm)

        result_events: list[ToolResultEvent] = []
        loop.run("test", on_tool_result=result_events.append)

        assert len(result_events) == 1
        assert result_events[0].name == "list_dir"
        assert isinstance(result_events[0].result, str)


# ---------------------------------------------------------------------------
# 流式输出
# ---------------------------------------------------------------------------

class TestStreamRun:
    def test_stream_run_yields_tokens(
        self, default_config: AgentConfig, empty_session: Session
    ):
        """stream_run() 直接回答时，逐 token yield 最终文本。"""
        default_config.llm.streaming = True
        resp = _StreamingLLMResponse(make_streaming_chunks("流式ABC"))
        llm = make_mock_llm(resp)
        loop = make_loop(default_config, empty_session, llm)

        tokens = list(loop.stream_run("你好"))
        full_text = "".join(tokens)
        assert full_text == "流式ABC"

    def test_stream_run_appends_final_message(
        self, default_config: AgentConfig, empty_session: Session
    ):
        """stream_run() 完成后，完整回答以 assistant 消息写入 session.messages。"""
        default_config.llm.streaming = True
        resp = _StreamingLLMResponse(make_streaming_chunks("完整回答"))
        llm = make_mock_llm(resp)
        loop = make_loop(default_config, empty_session, llm)

        list(loop.stream_run("问题"))  # 消费 generator
        assistant_msgs = [m for m in empty_session.messages if m["role"] == "assistant"]
        assert any("完整回答" in m.get("content", "") for m in assistant_msgs)


# ---------------------------------------------------------------------------
# change_dir 影响 tool_ctx
# ---------------------------------------------------------------------------

class TestToolCtxStateChange:
    def test_change_dir_updates_tool_ctx(
        self, default_config: AgentConfig, empty_session: Session, tmp_work_dir: Path
    ):
        """change_dir 工具执行后，loop.tool_ctx.work_dir 随之更新。"""
        # 创建子目录
        sub = tmp_work_dir / "newsub"
        sub.mkdir()

        r1 = tool_call_resp("cd1", "change_dir", f'{{"path": "{sub.as_posix()}"}}')
        r2 = blocking_resp("目录已切换")
        llm = make_mock_llm(r1, r2)
        loop = make_loop(default_config, empty_session, llm)
        loop.run("切换目录")

        assert loop.tool_ctx.work_dir == sub.resolve()
