"""手动测试入口：交互式对比手写 ReAct 与 LangGraph（真实 API）。

跑法：
    D:/anaconda3/python.exe -m agent.langgraph_impl.smoke_interactive

进入后输入问题，脚本会：
  1. 用同一问题分别跑手写 AgentLoop 和 LangGraphAgent
  2. 打印两边的回答、耗时、Token 用量
  3. 输入 q 退出

不改动任何业务代码，只做验证。
"""

from __future__ import annotations

import time

from ..config import load_config
from ..llm import LLMClient
from ..session import Session
from ..loop import AgentLoop
from .graph import LangGraphAgent


def main() -> int:
    config = load_config()
    print("=" * 64)
    print("LangGraph 迁移 · 手动对比测试")
    print(f"模型: {config.llm.model} @ {config.llm.base_url}")
    print("=" * 64)
    print("输入问题后回车，分别跑手写版和 LangGraph 版；输入 q 退出。\n")

    try:
        llm = LLMClient(config.llm)
    except Exception as exc:
        print(f"[错误] LLM 客户端初始化失败: {exc}")
        print("检查 .env 的 AGENT_API_KEY / AGENT_BASE_URL / AGENT_MODEL")
        return 1

    turn = 0
    while True:
        try:
            question = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            return 0
        if question.lower() in ("q", "quit", "exit"):
            print("退出。")
            return 0
        if not question:
            continue
        turn += 1

        # --- 手写版 ---
        hw_session = Session(
            id=f"manual-hw-{turn}", created_at="", updated_at="", messages=[]
        )
        hw_loop = AgentLoop(config, hw_session, llm)
        t0 = time.perf_counter()
        try:
            hw_answer = hw_loop.run(question)
        except Exception as exc:
            hw_answer = f"[出错] {type(exc).__name__}: {exc}"
        hw_ms = (time.perf_counter() - t0) * 1000
        hw_usage = dict(hw_loop.last_usage)
        hw_loop.close()

        # --- LangGraph 版 ---
        lg_session = Session(
            id=f"manual-lg-{turn}", created_at="", updated_at="", messages=[]
        )
        lg_agent = LangGraphAgent(config, lg_session, llm)
        t0 = time.perf_counter()
        try:
            lg_answer = lg_agent.run(question, thread_id=f"manual-{turn}")
        except Exception as exc:
            lg_answer = f"[出错] {type(exc).__name__}: {exc}"
        lg_ms = (time.perf_counter() - t0) * 1000
        lg_usage = dict(lg_agent.last_usage)
        lg_agent.close()

        # --- 输出对比 ---
        print("\n" + "-" * 64)
        print(f"[手写 ReAct]  {hw_ms:.0f} ms  usage={hw_usage}")
        print(hw_answer)
        print("-" * 64)
        print(f"[LangGraph]   {lg_ms:.0f} ms  usage={lg_usage}")
        print(lg_answer)
        print("-" * 64)
        print(f"延迟差: {lg_ms - hw_ms:+.0f} ms   回答一致: {hw_answer == lg_answer}")
        print()


if __name__ == "__main__":
    raise SystemExit(main())
