"""LangGraph StateGraph 版 ReAct 引擎。

与手写版 `agent/loop.py::AgentLoop.run` 一一对应：

    手写版 for 循环                    LangGraph StateGraph
    -------------------------------    -------------------------------
    messages = build_messages(...)  ->  State.messages（覆盖式流转）
    llm.chat_blocking(...)          ->  call_model 节点
    if not has_tool_calls: return   ->  should_continue -> END
    _execute_runtime_tool(...)      ->  execute_tools 节点（ToolNode 等价）
    session.messages.extend(...)    ->  节点返回完整 messages
    for iteration in range(max)     ->  iterations 计数 + 条件边

关键：LLM / ContextManager / 工具注册表 / 思考块剥离全部复用手写版组件，
本模块只负责"控制流"的迁移。这样 A/B 对比时唯一变量是控制流实现，
质量差异可直接归因到框架，而不是数据/提示词不同。
"""

from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from ..config import AgentConfig
from ..context import ContextManager
from ..llm import LLMClient
from ..loop import AgentLoop
from ..reasoning import ReasoningStats, strip_reasoning
from ..session import Session
from ..tools import get_openai_schemas_from_defs
from .state import AgentState

# 回调类型，与手写版对齐
OnToolCall = Callable[[Any], None]
OnToolResult = Callable[[Any], None]


class LangGraphAgent:
    """用 LangGraph 驱动的三节点 ReAct 图。"""

    def __init__(
        self,
        config: AgentConfig,
        session: Session,
        llm: LLMClient,
        skill_registry: Any = None,
        memory_store: Any = None,
        context_manager: ContextManager | None = None,
    ) -> None:
        self._config = config
        self._session = session
        self._llm = llm
        # 复用手写版依赖装配：MCP 启动、工具注册表、ToolContext、ContextManager
        self._loop = AgentLoop(
            config, session, llm,
            skill_registry=skill_registry,
            memory_store=memory_store,
            context_manager=context_manager,
        )
        self._tool_schemas = get_openai_schemas_from_defs(self._loop.runtime_tools)
        self.last_usage: dict[str, int] = {}
        self.last_output_stats: dict[str, Any] = {}
        self.graph = build_graph(self)

    @property
    def loop(self) -> AgentLoop:
        """暴露底层手写版实例，便于 A/B 对比时取 runtime_tools 等元数据。"""
        return self._loop

    def close(self) -> None:
        self._loop.close()

    # ------------------------------------------------------------------
    # 图节点
    # ------------------------------------------------------------------

    def _sync_state(self, state: AgentState) -> None:
        """把图的 messages 同步到 session，让 ContextManager 读到同一份历史。"""
        self._session.messages = list(state["messages"])

    def call_model(self, state: AgentState) -> dict[str, Any]:
        """节点：构建上下文 -> 调 LLM -> 追加 assistant 消息 -> 剥离思考块。"""
        self._sync_state(state)
        messages = self._loop._context_manager.build_messages(
            self._session, self._loop.tool_ctx
        )
        response = self._llm.chat_blocking(
            messages=messages,
            tools=self._tool_schemas or None,
        )

        message = response.to_message()
        if not response.has_tool_calls:
            # 最终回答：与手写版完全相同的思考块剥离逻辑
            content = response.content or ""
            clean, stats = strip_reasoning(content)
            if stats.empty_after_strip:
                message["content"] = content
            else:
                message["content"] = clean
        else:
            # 工具调用阶段：content 若带思考块也剥掉，避免下轮发散
            if message.get("content"):
                cleaned, stats = strip_reasoning(str(message["content"]))
                if not stats.empty_after_strip:
                    message["content"] = cleaned

        self._session.messages.append(message)
        usage = self._merge_usage(state.get("usage", {}), getattr(response, "usage", None))
        stats_merged = self._merge_stats(state.get("output_stats", {}), self._last_strip_stats(response, message))

        result: dict[str, Any] = {
            "messages": list(self._session.messages),
            "iterations": state["iterations"] + 1,
            "usage": usage,
            "output_stats": stats_merged,
        }
        if not response.has_tool_calls:
            result["final_answer"] = str(message["content"])
        return result

    def execute_tools(self, state: AgentState) -> dict[str, Any]:
        """节点：执行最后一条 assistant 消息里的全部 tool_calls。

        对应手写版 for tc in response.tool_calls 段。用手写版的
        _execute_runtime_tool 保证工具语义 100% 一致。
        """
        last = state["messages"][-1]
        tool_calls = last.get("tool_calls") or []
        new_messages = list(state["messages"])
        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {})
            call_id = tc.get("id", "")
            result = self._loop._execute_runtime_tool(name, args)
            new_messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": result,
            })
        self._session.messages = new_messages
        return {"messages": new_messages}

    def should_continue(self, state: AgentState) -> str:
        """条件边：有最终回答 -> END；达最大迭代 -> END；否则继续调工具。"""
        if state.get("final_answer"):
            return "end"
        if state["iterations"] >= state["max_iterations"]:
            return "guard"
        last = state["messages"][-1]
        return "tools" if last.get("tool_calls") else "end"

    def guard_max_iter(self, state: AgentState) -> dict[str, Any]:
        """节点：达最大迭代时的兜底回答（对应手写版末尾的警告文本）。"""
        warning = "[警告] 已达最大迭代次数，请检查任务是否过于复杂。"
        if not state.get("final_answer"):
            return {"final_answer": warning}
        return {}

    # ------------------------------------------------------------------
    # 统计辅助（对齐手写版 _absorb_usage / _absorb_stats）
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_usage(cur: dict[str, int], usage: dict[str, int] | None) -> dict[str, int]:
        if not usage:
            return cur
        out = dict(cur)
        out["prompt"] = int(out.get("prompt", 0)) + int(usage.get("prompt") or 0)
        out["completion"] = int(out.get("completion", 0)) + int(usage.get("completion") or 0)
        out["calls"] = int(out.get("calls", 0)) + 1
        return out

    @staticmethod
    def _merge_stats(cur: dict[str, Any], stats: ReasoningStats | None) -> dict[str, Any]:
        if stats is None:
            return cur
        out = dict(cur)
        out["blocks"] = int(out.get("blocks", 0)) + stats.blocks
        out["chars"] = int(out.get("chars", 0)) + stats.chars
        out["raw_chars"] = int(out.get("raw_chars", 0)) + stats.raw_chars
        out["clean_chars"] = int(out.get("clean_chars", 0)) + stats.clean_chars
        if stats.empty_after_strip:
            out["empty_after_strip"] = True
        return out

    @staticmethod
    def _last_strip_stats(response: Any, message: dict[str, Any]) -> ReasoningStats | None:
        """从本轮剥离处取统计。简化：重算一次（内容已在 message 里）。"""
        # 手写版在剥离时就拿了 stats；这里为保持节点纯净，按需重算原始 content。
        # 原始 content 未保留时返回 None，不影响答复正确性。
        raw = getattr(response, "content", "") or ""
        _, stats = strip_reasoning(raw)
        return stats

    # ------------------------------------------------------------------
    # 主接口
    # ------------------------------------------------------------------

    def run(self, user_input: str) -> str:
        """执行一轮（阻塞，非流式），返回最终回答。接口与 AgentLoop.run 一致。"""
        self.last_usage = {}
        self.last_output_stats = {}
        self._session.messages.append({"role": "user", "content": user_input})

        initial: AgentState = {
            "messages": list(self._session.messages),
            "iterations": 0,
            "max_iterations": self._config.max_iterations,
            "tool_schemas": self._tool_schemas,
            "final_answer": "",
            "usage": {},
            "output_stats": {},
        }
        result = self.graph.invoke(initial)
        self.last_usage = result.get("usage", {})
        self.last_output_stats = result.get("output_stats", {})
        self._session.messages = list(result["messages"])
        return str(result.get("final_answer", ""))


def build_graph(agent: LangGraphAgent) -> Any:
    """构建 StateGraph：START -> call_model -> {tools | END}。"""
    builder = StateGraph(AgentState)
    builder.add_node("call_model", agent.call_model)
    builder.add_node("execute_tools", agent.execute_tools)
    builder.add_node("guard_max_iter", agent.guard_max_iter)

    builder.add_edge(START, "call_model")
    builder.add_conditional_edges(
        "call_model",
        agent.should_continue,
        {"tools": "execute_tools", "end": END, "guard": "guard_max_iter"},
    )
    builder.add_edge("execute_tools", "call_model")
    builder.add_edge("guard_max_iter", END)
    return builder.compile()
