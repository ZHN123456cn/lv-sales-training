"""
后端启动脚本。

用法：
  python run_backend.py                # 默认 8000 端口，开启热重载
  python run_backend.py --port 9000    # 指定端口
  python run_backend.py --no-reload    # 关闭热重载
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 FastAPI 后端服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--no-reload", action="store_true", help="关闭热重载")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        reload_dirs=["backend", "agent"],
    )


if __name__ == "__main__":
    main()
