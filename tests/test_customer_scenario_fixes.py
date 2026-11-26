"""顾客模拟场景三项修复的回归测试。

覆盖：
  1. 成交检测在完整文本上判定（含标记 / 购买话术 / 兜底，不变形不误判）
  2. 顾客 session 的 AgentLoop 零工具（shell/write_file 不会穿帮）
  3. 后端思考块剥离 + flush 不吞尾字（前端已移除重复过滤器）
"""

from __future__ import annotations

from unittest.mock import Mock

from agent.loop import AgentLoop
from agent.reasoning import ReasoningFilter, strip_reasoning
from backend.routers.chat import detect_purchase_commitment


# ---------------------------------------------------------------------------
# 1. 成交检测
# ---------------------------------------------------------------------------

def test_purchase_marker_detected_anywhere():
    """标记出现在文本任意位置都能识别（不再依赖前端逐 token 切断检测）。"""
    assert detect_purchase_commitment("好的，就这款吧，帮我包起来。[[END_TRAINING]]")
    assert detect_purchase_commitment("[[END_TRAINING]]")
    assert detect_purchase_commitment("先看看……决定购买[[END_TRAINING]]谢谢")


def test_purchase_commitment_by_phrase():
    """模型没输出标记时，收尾性购买话术兜底仍能判定成交。"""
    assert detect_purchase_commitment("行，就这只吧，帮我包起来")
    assert detect_purchase_commitment("决定了，刷信用卡")
    assert detect_purchase_commitment("我买了，开票吧")
    assert detect_purchase_commitment("那我走了，谢谢你，下次来保养联系你")


def test_purchase_not_false_positive():
    """询价、犹豫不构成成交，避免过早结束演练。"""
    assert not detect_purchase_commitment("")
    assert not detect_purchase_commitment("这个多少钱？我先看看")
    assert not detect_purchase_commitment("我再考虑一下，预算有点超")
    assert not detect_purchase_commitment("包起来是什么意思？")
    # 意向表达不等于成交：客户说完这句还会继续看货，不能过早结束
    assert not detect_purchase_commitment("我想买就趁现在，不然回家又纠结")


# ---------------------------------------------------------------------------
# 2. 顾客场景零工具
# ---------------------------------------------------------------------------

def test_customer_loop_has_no_tools():
    """enabled_tools_override=[] 时，本地工具与 MCP 工具都不加载。"""
    # 用最小依赖构造 AgentLoop，只验证工具列表为空，不触发网络。
    from agent.config import AgentConfig

    config = AgentConfig()
    config.enabled_tools = ["shell", "write_file", "read_file"]
    config.mcp.enabled = True  # 即使全局开了 MCP，override=[] 也必须禁用

    loop = AgentLoop(
        config=config,
        session=Mock(),
        llm=Mock(),
        memory_store=Mock(),
        skill_registry=Mock(),
        enabled_tools_override=[],
    )
    assert loop.runtime_tools == []
    assert loop.enabled_tools == []
    loop.close()


# ---------------------------------------------------------------------------
# 3. 思考块剥离不吞尾字（修复前端吞字的根因保证）
# ---------------------------------------------------------------------------

def test_reasoning_flush_keeps_tail():
    """流结束时 flush 吐出残留，客户话末尾不丢字。"""
    f = ReasoningFilter()
    chunks = ["好的，就这", "款吧，帮我包", "起来。" ]
    visible = "".join(f.feed(c) for c in chunks) + f.flush()
    assert visible == "好的，就这款吧，帮我包起来。"


def test_reasoning_split_tag_and_marker():
    """<think> 跨 token 且不吞尾；标记作为普通文本原样透传给后端判定。"""
    clean, _ = strip_reasoning("<think>内部想法</think>我决定了，包起来。[[END_TRAINING]]")
    assert clean == "我决定了，包起来。[[END_TRAINING]]"

    f = ReasoningFilter()
    parts = ["<thi", "nk>隐藏</thi", "nk>结论：包起来。"]
    out = "".join(f.feed(p) for p in parts) + f.flush()
    assert out == "结论：包起来。"
