"""
一键启动前后端服务。

用法：
  python run_all.py

会同时启动：
  - 后端 FastAPI  (http://localhost:8000)
  - 前端 Vite     (http://localhost:5173)

按 Ctrl+C 停止所有服务。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def main() -> None:
    print("=" * 60)
    print("  LV 销售培训 Agent - 前后端一键启动")
    print("=" * 60)

    # 检查后端依赖
    try:
        import fastapi  # noqa: F401
    except ImportError:
        print("[后端] 安装 FastAPI 依赖...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(PROJECT_ROOT / "backend" / "requirements.txt")],
            check=True,
        )

    # 检查前端依赖
    frontend_dir = PROJECT_ROOT / "frontend"
    if not (frontend_dir / "node_modules").is_dir():
        print("[前端] 安装 npm 依赖...")
        subprocess.run(["npm", "install"], cwd=str(frontend_dir), check=True)

    print("\n[启动] 后端 FastAPI 服务 (端口 8000)...")
    backend_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"],
        cwd=str(PROJECT_ROOT),
    )

    print("[启动] 前端 Vite 开发服务器 (端口 5173)...\n")
    frontend_proc = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=str(frontend_dir),
    )

    print("-" * 60)
    print("  前端地址: http://localhost:5173")
    print("  后端地址: http://localhost:8000")
    print("  API 文档: http://localhost:8000/docs")
    print("  按 Ctrl+C 停止所有服务")
    print("-" * 60)

    try:
        backend_proc.wait()
        frontend_proc.wait()
    except KeyboardInterrupt:
        print("\n[停止] 正在关闭服务...")
        backend_proc.terminate()
        frontend_proc.terminate()
        try:
            backend_proc.wait(timeout=5)
            frontend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend_proc.kill()
            frontend_proc.kill()
        print("[停止] 已停止。")


if __name__ == "__main__":
    main()
