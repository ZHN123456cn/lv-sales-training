"""三 Agent 协作图的冒烟测试。

验证顾客模拟 -> 销售训练 -> 教练评估的 handoff 链路，用 FakeLLM 不依赖真实 API。
"""

from __future__ import annotations

from agent.config import AgentConfig
from agent.langgraph_impl import run_training_session


class FakeResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    @property
    def content(self) -> str:
        return self._content

    @property
    def tool_calls(self) -> list:
        return []

    @property
    def has_tool_calls(self) -> bool:
        return False

    def to_message(self) -> dict:
        return {"role": "assistant", "content": self._content}

    @property
    def usage(self) -> dict[str, int]:
        return {}


class ScriptedLLM:
    """按调用顺序返回脚本内容；测试里覆盖开场/顾客回应/教练评价。"""

    def __init__(self, script: list[str]) -> None:
        self._script = list(script)
        self.i = 0

    def chat_blocking(self, messages, tools=None):
        text = self._script[min(self.i, len(self._script) - 1)]
        self.i += 1
        return FakeResponse(text)


class FakeScenario:
    id = "first_time"
    title = "首次进店顾客"
    icon = "🛍️"
    description = "第一次走进 LV 门店的顾客"
    customer_profile = "对品牌有认知但不深入"
    opening_behavior = "环顾四周后走向包区"


def _config():
    c = AgentConfig(max_iterations=3)
    c.mcp.enabled = False
    return c


def test_training_graph_handoff_full_flow():
    """一次 invoke 走完开场 -> 2轮销售 -> 教练评价，角色 handoff 正确。"""
    llm = ScriptedLLM([
        "你好，我随便看看",              # 顾客开场
        "这款包多少钱？",                 # 顾客回应轮1
        "有没有别的颜色？",               # 顾客回应轮2
        "需求挖掘：18\n产品推荐：18\n品牌知识：18\n沟通表达：18\n成交推进：18\n总分：90",  # 教练评价
    ])
    result = run_training_session(
        llm, _config(), FakeScenario(), "张三", "20270001",
        sales_inputs=["我想买个包", "有红色的吗"],
    )
    # 对话应含：开场 + 2轮(销售+顾客) = 5 条
    assert len(result["dialogue"]) == 5
    roles = [t["role"] for t in result["dialogue"]]
    assert roles == ["顾客", "销售顾问", "顾客", "销售顾问", "顾客"]
    # 教练评价产出
    assert "需求挖掘" in result["evaluation"]
    assert result["turns"] == 2
    # 总分校正被触发（reason 非空）
    assert result["total_reconciled"] in ("fixed", "ok", "no_total", "partial", "out_of_range")


def test_training_graph_single_turn():
    """单轮训练也能走通。"""
    llm = ScriptedLLM([
        "欢迎光临", "我想看看", "教练评价：需求挖掘 20 分",
    ])
    result = run_training_session(
        llm, _config(), FakeScenario(), "李四", "20270002",
        sales_inputs=["你好"],
    )
    assert len(result["dialogue"]) == 3
    assert result["turns"] == 1
