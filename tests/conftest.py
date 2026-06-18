"""
公共 Fixture。

所有测试共用的基础构建块：
  - tmp_work_dir   : 临时工作目录（隔离文件系统）
  - tool_ctx       : 指向临时目录的 ToolContext
  - default_config : 无需真实 config.yaml 的 AgentConfig
  - session_mgr    : 指向临时目录的 SessionManager
  - make_session   : 工厂函数，生成带消息的 Session
  - mock_blocking_response / mock_tool_call_response : LLM mock 辅助
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent.config import AgentConfig, LLMConfig, SessionConfig
from agent.session import Session, SessionManager
from agent.skills import Skill, SkillRegistry
from agent.tools import ToolContext


# ---------------------------------------------------------------------------
# 基础目录 fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_work_dir(tmp_path: Path) -> Path:
    """临时工作目录，每个测试独立隔离。"""
    work = tmp_path / "work"
    work.mkdir()
    return work


@pytest.fixture
def tool_ctx(tmp_work_dir: Path) -> ToolContext:
    """指向临时目录的 ToolContext。"""
    return ToolContext(work_dir=tmp_work_dir, session_id="test-session-001")


@pytest.fixture
def skill_registry(tmp_path: Path) -> SkillRegistry:
    """包含一个测试 Skill 的 SkillRegistry。"""
    skill_dir = tmp_path / "skills" / "research"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: research\ndescription: Research tasks.\n---\n\nUse references/ and scripts/ when needed.\n",
        encoding="utf-8",
    )
    registry = SkillRegistry()
    registry.register(Skill(
        name="research",
        description="Research tasks.",
        file_path=skill_file,
        base_dir=skill_dir,
    ))
    return registry


# ---------------------------------------------------------------------------
# 配置 fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def default_config(tmp_work_dir: Path, tmp_path: Path) -> AgentConfig:
    """
    测试用 AgentConfig：
    - 不依赖真实 config.yaml / .env
    - 工作目录、session 目录均指向临时路径
    """
    cfg = AgentConfig()
    cfg.llm.api_key = "test-api-key"
    cfg.llm.streaming = False          # 测试中默认关闭流式，减少 mock 复杂度
    cfg.work_dir = str(tmp_work_dir)
    cfg.project_root = tmp_work_dir    # 让 resolved_* 方法都落在临时目录
    cfg.session.dir = str(tmp_path / ".sessions")
    cfg.skills.dirs = [str(tmp_path / "skills")]
    cfg.max_iterations = 5             # 测试时限小迭代数，加速失败检测
    return cfg


# ---------------------------------------------------------------------------
# Session fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def session_mgr(tmp_path: Path) -> SessionManager:
    """指向临时目录的 SessionManager。"""
    return SessionManager(tmp_path / ".sessions")


@pytest.fixture
def empty_session() -> Session:
    """无消息的新 Session。"""
    return Session(
        id="20240420-120000-aabbcc",
        created_at="2024-04-20T12:00:00Z",
        updated_at="2024-04-20T12:00:00Z",
    )


@pytest.fixture
def session_with_messages() -> Session:
    """含一轮对话历史的 Session。"""
    s = Session(
        id="20240420-120000-ddeeff",
        created_at="2024-04-20T12:00:00Z",
        updated_at="2024-04-20T12:00:00Z",
    )
    s.messages = [
        {"role": "system", "content": "你是一个助手。"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！有什么我可以帮你的？"},
    ]
    return s


# ---------------------------------------------------------------------------
# LLM mock 辅助函数
# ---------------------------------------------------------------------------

def make_blocking_completion(
    content: str,
    tool_calls: list[dict] | None = None,
    reasoning_content: str | None = None,
) -> Any:
    """
    构造一个仿 OpenAI ChatCompletion 对象（非流式）。

    tool_calls 格式：
      [{"id": "call_1", "name": "tool_name", "arguments": '{"key": "val"}'}]
    """
    msg = SimpleNamespace(
        content=content,
        tool_calls=None,
    )
    if reasoning_content is not None:
        msg.reasoning_content = reasoning_content
    if tool_calls:
        msg.tool_calls = [
            SimpleNamespace(
                id=tc["id"],
                function=SimpleNamespace(
                    name=tc["name"],
                    arguments=tc["arguments"],  # JSON 字符串
                ),
            )
            for tc in tool_calls
        ]
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def make_streaming_chunks(content: str) -> list[Any]:
    """
    构造仿流式 chunk 列表（纯文本，无工具调用）。
    将 content 拆成每字一个 chunk。
    """
    chunks = []
    for ch in content:
        delta = SimpleNamespace(content=ch, tool_calls=None)
        choice = SimpleNamespace(delta=delta)
        chunks.append(SimpleNamespace(choices=[choice]))
    # 末尾添加 finish chunk（delta.content 为空）
    delta_end = SimpleNamespace(content=None, tool_calls=None)
    chunks.append(SimpleNamespace(choices=[SimpleNamespace(delta=delta_end)]))
    return chunks


def make_streaming_tool_call_chunks(
    tool_id: str, tool_name: str, arguments: str
) -> list[Any]:
    """
    构造仿流式 tool_call chunk 列表。
    OpenAI 流式 tool_call 分多个 chunk 发送，这里简化为一个 chunk 包含完整信息。
    """
    tc_delta = SimpleNamespace(
        index=0,
        id=tool_id,
        function=SimpleNamespace(name=tool_name, arguments=arguments),
    )
    delta = SimpleNamespace(content=None, tool_calls=[tc_delta])
    choice = SimpleNamespace(delta=delta)
    return [SimpleNamespace(choices=[choice])]


# 导出辅助函数供测试文件直接使用
__all__ = [
    "make_blocking_completion",
    "make_streaming_chunks",
    "make_streaming_tool_call_chunks",
]
