"""
FastAPI 依赖注入。

提供对 AgentManager 和 SessionManager 的访问。
SessionManager 使用固定的 .sessions 目录（不随 agent 切换），
确保培训 session 在 customer/coach agent 切换后仍能正确加载。
AgentManager 是全局单例。
"""

from __future__ import annotations

from fastapi import Depends

from agent.session import SessionManager

from .core.agent_manager import AgentManager


def get_agent_manager() -> AgentManager:
    """全局 AgentManager 单例。"""
    return AgentManager.get_instance()


def get_session_manager(
    manager: AgentManager = Depends(get_agent_manager),
) -> SessionManager:
    """返回固定的 SessionManager（项目根目录 .sessions）。"""
    return manager.session_manager
