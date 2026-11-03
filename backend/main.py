"""
FastAPI 应用入口。

启动方式：
  cd "f:\\lv销售培训 - 副本"
  uvicorn backend.main:app --reload --port 8000

或使用启动脚本：
  python run_backend.py
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .core.agent_manager import AgentManager
from .routers import agents, chat, sessions, tools, training


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化 AgentManager，关闭时清理资源。"""
    # 启动
    manager = AgentManager.get_instance()
    app.state.agent_manager = manager
    yield
    # 关闭
    manager.close_all_loops()


app = FastAPI(
    title="LV 销售培训 Agent API",
    description="基于 ReAct Agent 的 LV 销售培训系统后端 API",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS：允许前端开发服务器跨域访问
# ---------------------------------------------------------------------------
cors_origins = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 注册路由
# ---------------------------------------------------------------------------
app.include_router(chat.router)
app.include_router(sessions.router)
app.include_router(agents.router)
app.include_router(tools.router)
app.include_router(training.router)


@app.get("/api/health")
async def health():
    """健康检查。"""
    return {"status": "ok", "service": "lv-sales-training-agent"}


# ---------------------------------------------------------------------------
# 生产环境：挂载前端静态文件（frontend/dist）
# ---------------------------------------------------------------------------
_frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """SPA 回退：所有非 API 路由返回 index.html。"""
        # API 路由不走这里（已被前面的路由匹配）
        file_path = _frontend_dist / full_path
        if full_path and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_frontend_dist / "index.html")
