"""探针：验证 /evaluate 的完整评价链路能否产出报告（不启服务）。

跑法：cd 项目根 && python scripts/probe_evaluate.py
目的：这是 2026-11 那两个 P0（长输出必断 / 流式解码崩）的验收脚本。
      它不走 HTTP，直接复用生产路由的同一批函数，避免"服务能启但接口不通"。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.agent_manager import AgentManager  # noqa: E402
from backend.training_scenarios import (  # noqa: E402
    COACH_EVALUATION_DIMENSIONS,
    SCENARIOS,
    build_evaluation_prompt,
    get_scenario,
    reconcile_report_total,
)
from agent.llm import LLMClient  # noqa: E402
from agent.session import SessionManager  # noqa: E402

DIALOGUE = """销售顾问：您好，欢迎光临LV，请问有什么可以帮您？
客户：我随便看看。
销售顾问：好的，您慢慢看，有需要随时叫我。这款Capucines是今年的新款，手工缝制，您要不要上手感受一下？
客户：这个多少钱？
销售顾问：这款是34500元。它的皮质是Taurillon小牛皮，非常柔软，而且五金是做旧的。
客户：有点超预算了，我本来想着两万左右。
销售顾问：理解，那您看看这款Alma BB，两万出头，经典款式很百搭，日常通勤也合适。
客户：这个倒是不错，不过我再逛逛吧。
销售顾问：没问题，我给您留个名片，您要是想再看看可以随时联系我，我们也可以帮您预留。
客户：好的，谢谢。"""


def main() -> int:
    scenario = get_scenario("first_time") or SCENARIOS[0]
    print(f"[1/4] scenario = {scenario.id} / {scenario.title}")

    prompt = build_evaluation_prompt(scenario, "测试学员", "2026001", DIALOGUE)
    print(f"[2/4] evaluation prompt = {len(prompt)} 字")

    llm = LLMClient(__import__("agent.config", fromlist=["load_config"]).load_config().llm)
    print(f"[3/4] transport_injected = {llm.transport_injected}")

    mgr = AgentManager()
    script = mgr._config.session.dir if hasattr(mgr._config.session, "dir") else ".sessions"
    sm = SessionManager(Path(script))
    mgr.switch_agent("coach")
    session = sm.new_session(work_dir="agent:coach")
    loop = mgr.get_or_create_loop(session)

    t0 = time.time()
    try:
        text = loop.run(prompt)
    except Exception as exc:  # noqa: BLE001
        print(f"[4/4] FAIL {time.time() - t0:.1f}s {type(exc).__name__}: {exc}")
        return 1
    dt = time.time() - t0

    # 与生产 /evaluate 走同一条校正：总分由维度之和决定（F-20261116-04）。
    text, total_info = reconcile_report_total(text, COACH_EVALUATION_DIMENSIONS)

    # 维度名必须以 workspace_coach/IDENTITY.md「评价维度」为准。
    # 早期版本按口头约定写成"产品推荐/品牌知识/成交"，与源文件不符，
    # 导致报告明明合格却被判成 3/5——契约的锚点是文件，不是记忆。
    dims = COACH_EVALUATION_DIMENSIONS
    hit = [d for d in dims if d in text]
    print(f"[4/4] OK {dt:.1f}s chars={len(text)} usage={loop.last_usage}")
    print(f"      总分校正：{total_info.get('reason')}"
          f"（报告 {total_info.get('reported')} / 实际 {total_info.get('computed')}）")
    print(f"      维度命中 {len(hit)}/5: {hit}")
    print(f"      可见 <think> 数量: {text.count('<think>')}")
    print("      ---- 前 300 字 ----")
    print("      " + text[:300].replace("\n", "\n      "))
    return 0 if len(hit) >= 4 else 2


if __name__ == "__main__":
    raise SystemExit(main())
