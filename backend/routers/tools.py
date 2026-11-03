"""
工具与 Skill 信息路由。

GET /api/tools    列出当前 Agent 可用的工具
GET /api/skills   列出当前 Agent 可用的 Skill
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agent.tools import TOOL_REGISTRY

from ..core.agent_manager import AgentManager
from ..dependencies import get_agent_manager

router = APIRouter(prefix="/api", tags=["tools"])


@router.get("/tools")
async def list_tools(
    manager: AgentManager = Depends(get_agent_manager),
):
    """列出启用的本地工具与 MCP 工具。"""
    # 构建一个临时 loop 来获取 MCP 工具（如果尚未缓存 loop，则惰性创建）
    # 为避免每请求创建 loop，这里只展示本地工具 + MCP 服务器配置
    enabled = manager.config.enabled_tools
    local_tools = []
    for name, td in TOOL_REGISTRY.items():
        local_tools.append({
            "name": name,
            "description": td.description,
            "enabled": name in enabled,
            "type": "local",
        })

    mcp_servers = []
    if manager.config.mcp.enabled:
        for name, server in manager.config.mcp.servers.items():
            mcp_servers.append({
                "name": name,
                "transport": server.transport,
                "command": server.command,
                "url": server.url,
            })

    return {
        "local_tools": local_tools,
        "mcp_enabled": manager.config.mcp.enabled,
        "mcp_servers": mcp_servers,
    }


@router.get("/skills")
async def list_skills(
    manager: AgentManager = Depends(get_agent_manager),
):
    """列出当前 Agent 可用的 Skill 摘要。"""
    skills = manager.skill_registry.list_for_prompt()
    return {
        "skills": [
            {
                "name": s.name,
                "description": s.description,
            }
            for s in skills
        ],
    }


@router.get("/config")
async def get_config(
    manager: AgentManager = Depends(get_agent_manager),
):
    """返回当前配置的非敏感信息。"""
    cfg = manager.config
    return {
        "llm": {
            "provider": cfg.llm.provider,
            "model": cfg.llm.model,
            "base_url": cfg.llm.base_url,
            "temperature": cfg.llm.temperature,
            "streaming": cfg.llm.streaming,
        },
        "agent": {
            "max_iterations": cfg.max_iterations,
            "work_dir": str(cfg.resolved_work_dir()),
        },
        "memory": {
            "enabled": cfg.memory.enabled,
        },
        "mcp": {
            "enabled": cfg.mcp.enabled,
        },
    }
