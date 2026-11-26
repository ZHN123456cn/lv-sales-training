"""
Session 管理路由。

GET    /api/sessions          列出所有 Session 元信息
POST   /api/sessions          创建新 Session
GET    /api/sessions/{id}     获取单个 Session 详情（含消息历史）
DELETE /api/sessions/{id}    删除 Session
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agent.session import Session, SessionManager

from ..core.agent_manager import AgentManager
from ..dependencies import get_agent_manager, get_session_manager

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionCreate(BaseModel):
    work_dir: str | None = None


def _session_to_dict(session: Session) -> dict:
    return {
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "work_dir": session.work_dir,
        "messages": [
            {
                "role": m.get("role"),
                "content": m.get("content"),
            }
            for m in session.messages
            if m.get("role") in ("user", "assistant", "tool")
        ],
    }


@router.get("")
async def list_sessions(
    session_mgr: SessionManager = Depends(get_session_manager),
):
    """列出当前 Agent 下的所有 Session（按更新时间降序）。"""
    metas = session_mgr.list_sessions()
    return {"sessions": [m.to_dict() for m in metas]}


@router.post("")
async def create_session(
    req: SessionCreate,
    manager: AgentManager = Depends(get_agent_manager),
    session_mgr: SessionManager = Depends(get_session_manager),
):
    """创建新 Session。"""
    work_dir = req.work_dir or str(manager.config.resolved_work_dir())
    session = session_mgr.new_session(work_dir=work_dir)
    session_mgr.save_session(session)
    return _session_to_dict(session)


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    session_mgr: SessionManager = Depends(get_session_manager),
):
    """获取 Session 详情（含消息历史）。"""
    try:
        session = session_mgr.load_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Session 不存在：{session_id}")
    return _session_to_dict(session)


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    manager: AgentManager = Depends(get_agent_manager),
    session_mgr: SessionManager = Depends(get_session_manager),
):
    """删除 Session，并清理对应的 AgentLoop 缓存。"""
    ok = session_mgr.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Session 不存在：{session_id}")
    manager.invalidate_loop(session_id)
    return {"deleted": session_id}
