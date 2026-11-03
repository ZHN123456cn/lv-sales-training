"""
聊天路由：SSE 流式对话接口。

POST /api/chat/stream
  Request:  {"session_id": "可选", "message": "用户消息"}
  Response: text/event-stream
    event: token       data: {"content": "..."}
    event: tool_call   data: {"name": "...", "arguments": {...}}
    event: tool_result data: {"name": "...", "result": "..."}
    event: done        data: {"session_id": "..."}
    event: error       data: {"message": "..."}

并发保护：同一 session 同时只能处理一个请求（busy 锁）。
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.loop import ToolCallEvent, ToolResultEvent
from agent.session import Session, SessionManager

from ..core.agent_manager import AgentManager
from ..dependencies import get_agent_manager, get_session_manager

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户消息")
    session_id: str | None = Field(None, description="Session ID，为空则创建新对话")


def _sse_event(event: str, data: dict[str, Any]) -> str:
    """格式化一个 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _run_agent_stream(
    manager: AgentManager,
    session: Session,
    message: str,
    session_mgr: SessionManager,
):
    """
    同步生成器：驱动 AgentLoop.stream_run，逐事件 yield SSE 字符串。

    AgentLoop.stream_run 的回调（on_tool_call/on_tool_result）运行在
    worker 线程中，无法直接 yield，因此通过 queue 桥接：worker 线程
    把事件放入队列，主生成器逐个取出并 yield。

    StreamingResponse 会自动将同步生成器放入线程池迭代，不阻塞事件循环。
    """
    event_queue: "queue.Queue[tuple[str, dict[str, Any]] | None]" = queue.Queue()
    error_holder: list[Exception] = []

    def _on_tool_call(e: ToolCallEvent) -> None:
        args = e.arguments
        args_str = json.dumps(args, ensure_ascii=False)
        if len(args_str) > 1000:
            args = {"_truncated": args_str[:1000]}
        event_queue.put(("tool_call", {
            "name": e.name,
            "arguments": args,
            "call_id": e.call_id,
        }))

    def _on_tool_result(e: ToolResultEvent) -> None:
        result = e.result
        truncated = False
        if len(result) > 1500:
            result = result[:1500]
            truncated = True
        event_queue.put(("tool_result", {
            "name": e.name,
            "call_id": e.call_id,
            "result": result,
            "truncated": truncated,
        }))

    def _worker() -> None:
        try:
            loop = manager.get_or_create_loop(session)
            for token in loop.stream_run(
                message,
                on_tool_call=_on_tool_call,
                on_tool_result=_on_tool_result,
            ):
                event_queue.put(("token", {"content": token}))
            event_queue.put(None)  # 结束标记
        except Exception as exc:  # noqa: BLE001
            error_holder.append(exc)
            event_queue.put(None)

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    try:
        while True:
            try:
                item = event_queue.get(timeout=120)
            except queue.Empty:
                yield _sse_event("error", {"message": "Agent 响应超时（120s）"})
                return
            if item is None:
                break
            event_type, data = item
            yield _sse_event(event_type, data)

        if error_holder:
            raise error_holder[0]

        # 自动保存 session
        if manager.config.session.auto_save:
            session_mgr.save_session(session)

        yield _sse_event("done", {"session_id": session.id})

    except Exception as exc:  # noqa: BLE001
        yield _sse_event("error", {"message": str(exc)})
    finally:
        manager.release_session(session.id)


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    manager: AgentManager = Depends(get_agent_manager),
    session_mgr: SessionManager = Depends(get_session_manager),
):
    """
    SSE 流式聊天接口。

    - 若 `session_id` 为空，自动创建新 Session。
    - 同一 session 同时只能有一个流式请求（防并发错乱）。
    - 返回 Server-Sent Events 流，包含 token / tool_call / tool_result / done / error。
    """
    # 解析 session
    if req.session_id:
        try:
            session = session_mgr.load_session(req.session_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Session 不存在：{req.session_id}")
        # 恢复 session 绑定的 agent 上下文（避免全局切换导致的上下文泄漏）
        # training/start 创建的 session work_dir 为 "agent:customer"
        if session.work_dir and session.work_dir.startswith("agent:"):
            agent_id = session.work_dir.split(":", 1)[1]
            if agent_id:
                manager.switch_agent(agent_id)
    else:
        session = session_mgr.new_session(work_dir=str(manager.config.resolved_work_dir()))

    # 并发保护：同一 session 不允许同时处理两个请求
    if not manager.try_acquire_session(session.id):
        raise HTTPException(
            status_code=409,
            detail="该会话正在处理其他请求，请等待当前回复完成后再发送。",
        )

    generator = _run_agent_stream(manager, session, req.message, session_mgr)

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
