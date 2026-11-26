"""LangGraph 迁移的冒烟测试。

验证 StateGraph 能复刻手写版的 ReAct 控制流：
  1. 无工具调用 -> 直接产出 final_answer
  2. 有工具调用 -> execute_tools -> 再调模型 -> 产出 final_answer
  3. 达最大迭代 -> 兜底警告

用 mock LLM，不依赖真实 API，保证 CI 可重复。
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.config import AgentConfig
from agent.langgraph_impl import LangGraphAgent
from agent.session import Session


class FakeResponse:
    """模拟 LLMResponse 的最小接口。"""

    def __init__(self, content: str, tool_calls: list | None = None) -> None:
        self._content = content
        self._tool_calls = tool_calls or []

    @property
    def content(self) -> str:
        return self._content

    @property
    def tool_calls(self) -> list:
        return self._tool_calls

    @property
    def has_tool_calls(self) -> bool:
        return bool(self._tool_calls)

    def to_message(self) -> dict:
        msg = {"role": "assistant", "content": self._content}
        if self._tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": __import__("json").dumps(tc["arguments"]),
                    },
                }
                for tc in self._tool_calls
            ]
        return msg

    @property
    def usage(self) -> dict[str, int]:
        return {"prompt": 10, "completion": 5}


class FakeLLM:
    """按脚本依次返回响应。"""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def chat_blocking(self, messages, tools=None):
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]


def _make_agent(responses, max_iterations=5):
    config = AgentConfig(max_iterations=max_iterations)
    config.mcp.enabled = False  # 冒烟测试不启动 MCP
    session = Session(id="smoke", created_at="", updated_at="", messages=[])
    llm = FakeLLM(responses)
    agent = LangGraphAgent(config, session, llm)
    return agent, llm


def test_no_tool_call_returns_answer():
    """无工具调用：一次 call_model 后直接 END。"""
    agent, llm = _make_agent([FakeResponse("你好，这是最终回答")])
    answer = agent.run("在吗")
    assert answer == "你好，这是最终回答"
    assert llm.calls == 1
    assert agent.last_usage["calls"] >= 1
    agent.close()


def test_tool_call_then_answer():
    """有工具调用：call_model -> execute_tools -> call_model -> END。"""
    tool_call = [{
        "id": "call_1",
        "name": "list_dir",
        "arguments": {"path": "."},
    }]
    agent, llm = _make_agent([
        FakeResponse("我先看看目录", tool_calls=tool_call),
        FakeResponse("目录看完了，这是回答"),
    ])
    answer = agent.run("列一下文件")
    assert answer == "目录看完了，这是回答"
    assert llm.calls == 2
    # 历史里应含 tool 消息
    assert any(m.get("role") == "tool" for m in agent._session.messages)
    agent.close()


def test_max_iteration_guard():
    """一直调工具，达到 max_iterations 后返回兜底警告。"""
    tool_call = [{
        "id": "call_x",
        "name": "list_dir",
        "arguments": {"path": "."},
    }]
    agent, llm = _make_agent(
        [FakeResponse("继续调用", tool_calls=tool_call)],
        max_iterations=3,
    )
    answer = agent.run("循环测试")
    assert "最大迭代" in answer
    assert llm.calls == 3
    agent.close()
