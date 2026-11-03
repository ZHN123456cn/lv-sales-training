"""
Agent 管理路由。

GET  /api/agents              列出所有 Agent
GET  /api/agents/current      获取当前 Agent
POST /api/agents/{id}/switch  切换当前 Agent
POST /api/agents/create       创建新 Agent workspace
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agent.agents import (
    AgentInfo,
    ensure_agent_workspace,
    list_agents,
    normalize_agent_id,
    scope_for,
    workspace_exists,
)

from ..core.agent_manager import AgentManager
from ..dependencies import get_agent_manager

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentCreate(BaseModel):
    name: str


def _agent_to_info(agent: AgentInfo, current_id: str) -> dict:
    return {
        "id": agent.id,
        "workspace_dir": str(agent.workspace_dir),
        "is_current": agent.id == current_id,
    }


@router.get("")
async def get_agents(
    manager: AgentManager = Depends(get_agent_manager),
):
    """列出所有可用 Agent。"""
    agents = list_agents(manager.config.project_root)
    current = manager.current_agent_id
    return {"agents": [_agent_to_info(a, current) for a in agents]}


@router.get("/current")
async def get_current_agent(
    manager: AgentManager = Depends(get_agent_manager),
):
    """获取当前 Agent 信息。"""
    scope = manager.config.agent_scope
    return {
        "id": manager.current_agent_id,
        "workspace_dir": str(scope.workspace_dir) if scope else None,
    }


@router.post("/create")
async def create_agent(
    req: AgentCreate,
    manager: AgentManager = Depends(get_agent_manager),
):
    """创建新 Agent workspace（不自动切换）。"""
    try:
        agent_id = normalize_agent_id(req.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    scope = scope_for(manager.config.project_root, agent_id)
    existed = workspace_exists(manager.config.project_root, agent_id)
    ensure_agent_workspace(scope)
    return {
        "id": agent_id,
        "workspace_dir": str(scope.workspace_dir),
        "existed": existed,
    }


@router.post("/{agent_id}/switch")
async def switch_agent(
    agent_id: str,
    manager: AgentManager = Depends(get_agent_manager),
):
    """切换当前 Agent（非交互式）。会清空已缓存的 AgentLoop。"""
    try:
        normalized = normalize_agent_id(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not workspace_exists(manager.config.project_root, normalized):
        raise HTTPException(status_code=404, detail=f"Agent 不存在：{normalized}")

    manager.switch_agent(normalized)
    return {"switched": normalized}
