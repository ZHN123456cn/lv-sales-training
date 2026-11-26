"""
前端启动脚本（Vue3 + Vite）。

用法：
  python run_frontend.py            # 默认 5173 端口
  python run_frontend.py --port 3000
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="启动前端 Vite 开发服务器")
    parser.add_argument("--port", type=int, default=5173, help="监听端口")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    args = parser.parse_args()

    frontend_dir = Path(__file__).resolve().parent / "frontend"
    if not (frontend_dir / "node_modules").is_dir():
        print("[提示] 未检测到 node_modules，正在安装依赖...")
        subprocess.run(["npm", "install"], cwd=str(frontend_dir), check=True)

    cmd = ["npm", "run", "dev", "--", "--port", str(args.port), "--host", args.host]
    print(f"[启动] {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(frontend_dir))


if __name__ == "__main__":
    main()
