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
from ..training_scenarios import build_scenario_brief, get_scenario

router = APIRouter(prefix="/api/chat", tags=["chat"])

# 顾客决定购买时的兜底信号。IDENTITY.md 要求顾客在末尾输出 [[END_TRAINING]]，
# 但模型偶尔不遵守（标记缺失或跨 token 被吞）。这里在**后端完整文本**上做一次
# 兜底判定——后端看到的是全量 token，不存在前端逐片段检测被切断的问题。
END_TRAINING_MARKER = "[[END_TRAINING]]"
# 只用「明确决策语句」做兜底，不用裸词"包起来"——否则"包起来是什么意思？"
# 这类询价/理解性问题会误判，过早结束演练。
# 只用「收尾性」成交话术：意向表达（如"我想买就趁现在"）不算，
# 否则客户还在看货就会被过早结束演练。精确触发仍靠 [[END_TRAINING]] 标记。
_PURCHASE_SIGNALS = (
    "帮我包起来", "包起来吧", "就这款吧", "就这只吧", "就买这个",
    "我买了", "我就要这个", "决定了", "刷信用卡", "刷卡吧",
    "开票吧", "那就定这只", "就它了", "那我走了", "拿全新的看看",
)


def detect_purchase_commitment(text: str) -> bool:
    """判断顾客文本里是否含明确成交信号（标记本身或购买话术）。"""
    if not text:
        return False
    upper = text.upper()
    if END_TRAINING_MARKER.upper() in upper:
        return True
    return any(signal in text for signal in _PURCHASE_SIGNALS)


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
    # 顾客模拟场景：工具事件不推给前端（角色沉浸），但在服务端仍完整记录回答，
    # 用于兜底检测成交信号。
    is_customer = session.work_dir == "agent:customer"
    full_text_parts: list[str] = []

    def _on_tool_call(e: ToolCallEvent) -> None:
        if is_customer:
            return  # 顾客 Agent 的工具调用是内部行为，不暴露给学员
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
        if is_customer:
            return
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
                full_text_parts.append(token)
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

        done_data: dict[str, Any] = {"session_id": session.id}
        # 顾客 Agent：在完整文本上统一判定成交，避免前端逐片段检测被切断。
        if is_customer and detect_purchase_commitment("".join(full_text_parts)):
            done_data["purchase_detected"] = True
        yield _sse_event("done", done_data)

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
        # 场景正文随进程重启会丢（存在 config 内存映射里），这里按 session
        # 记住的 scenario_id 补登记。不补的话：服务重启后继续同一会话，
        # 顾客又变成"没有场景的通用顾客"，且没有任何报错。
        if session.scenario_id and session.scenario_id not in manager.config.scenario_prompts:
            scenario = get_scenario(session.scenario_id)
            if scenario is not None:
                manager.register_scenario_prompt(
                    scenario.id, build_scenario_brief(scenario)
                )
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
