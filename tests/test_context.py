from __future__ import annotations

from agent.config import AgentConfig
from agent.context import ContextManager
from agent.agents import scope_for
from agent.session import Session
from agent.tools import ToolContext


def test_context_includes_system_prompt(default_config: AgentConfig, empty_session: Session, tool_ctx: ToolContext, skill_registry):
    manager = ContextManager(default_config, skill_registry)

    messages = manager.build_messages(empty_session, tool_ctx)

    assert messages[0]["role"] == "system"
    assert "你是一个智能助手" in messages[0]["content"]


def test_context_includes_available_skills(default_config: AgentConfig, empty_session: Session, tool_ctx: ToolContext, skill_registry):
    manager = ContextManager(default_config, skill_registry)

    messages = manager.build_messages(empty_session, tool_ctx)

    assert "<available_skills>" in messages[0]["content"]
    assert "<name>research</name>" in messages[0]["content"]
    assert "<description>Research tasks.</description>" in messages[0]["content"]


def test_context_does_not_persist_system_prompt(default_config: AgentConfig, empty_session: Session, tool_ctx: ToolContext, skill_registry):
    manager = ContextManager(default_config, skill_registry)

    manager.build_messages(empty_session, tool_ctx)

    assert empty_session.messages == []


def test_context_includes_history(default_config: AgentConfig, empty_session: Session, tool_ctx: ToolContext, skill_registry):
    empty_session.messages.append({"role": "user", "content": "hello"})
    manager = ContextManager(default_config, skill_registry)

    messages = manager.build_messages(empty_session, tool_ctx)

    assert messages[-1] == {"role": "user", "content": "hello"}


def test_context_mentions_skill_read_rule(default_config: AgentConfig, empty_session: Session, tool_ctx: ToolContext, skill_registry):
    manager = ContextManager(default_config, skill_registry)

    messages = manager.build_messages(empty_session, tool_ctx)

    assert "skill_read" in messages[0]["content"]
    assert "SKILL.md" in messages[0]["content"]


def test_context_includes_agent_scope_and_identity(default_config: AgentConfig, empty_session: Session, tool_ctx: ToolContext, skill_registry, tmp_path):
    default_config.agent_scope = scope_for(tmp_path, "work")
    default_config.identity_text = "我是 work agent。"
    manager = ContextManager(default_config, skill_registry)

    messages = manager.build_messages(empty_session, tool_ctx)

    system = messages[0]["content"]
    assert "Agent ID: work" in system
    assert str(default_config.agent_scope.workspace_dir) in system
    assert "<identity>" in system
    assert "我是 work agent。" in system
