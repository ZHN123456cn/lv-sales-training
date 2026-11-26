"""LangGraph StateGraph 版 ReAct 引擎。

与手写版 `agent/loop.py::AgentLoop.run` 一一对应：

    手写版 for 循环                    LangGraph StateGraph
    -------------------------------    -------------------------------
    messages = build_messages(...)  ->  State.messages（覆盖式流转）
    llm.chat_blocking(...)          ->  call_model 节点
    strip_reasoning(最终回答)        ->  strip_reasoning 节点（独立）
    _execute_runtime_tool(...)      ->  execute_tools 节点（ToolNode 等价）
    session.messages.extend(...)    ->  节点返回完整 messages
    for iteration in range(max)     ->  iterations 计数 + 条件边
    Session 持久化                   ->  checkpointer（MemorySaver）

关键：LLM / ContextManager / 工具注册表 / 思考块剥离全部复用手写版组件，
本模块只负责"控制流"的迁移。这样 A/B 对比时唯一变量是控制流实现，
质量差异可直接归因到框架，而不是数据/提示词不同。

checkpointer 的用法：thread_id 标识会话。run() 先从 checkpointer 读取该
thread 的状态快照，与新的 messages 合并后 invoke，结果再存回同一 thread。
这实现了"同一 thread_id 下问两轮，第二轮记得第一轮"，与手写版 Session 对应。
"""

from __future__ import annotations

from typing import Any, Callable

from langgraph.checkpoint.memory import MemorySaver
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
    """用 LangGraph 驱动的 ReAct 图（call_model / strip_reasoning / execute_tools）。"""

    def __init__(
        self,
        config: AgentConfig,
        session: Session,
        llm: LLMClient,
        skill_registry: Any = None,
        memory_store: Any = None,
        context_manager: ContextManager | None = None,
        checkpointer: Any = None,
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
        self.checkpointer = checkpointer if checkpointer is not None else MemorySaver()
        self.graph = build_graph(self, self.checkpointer)

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
        """节点：构建上下文 -> 调 LLM -> 追加 assistant 消息（原始内容）。

        思考块剥离不在此处做，而是交给独立的 strip_reasoning 节点，
        对应清单里"strip_reasoning 独立 node"的映射。
        """
        self._sync_state(state)
        messages = self._loop._context_manager.build_messages(
            self._session, self._loop.tool_ctx
        )
        response = self._llm.chat_blocking(
            messages=messages,
            tools=self._tool_schemas or None,
        )

        message = response.to_message()
        self._session.messages.append(message)
        usage = self._merge_usage(state.get("usage", {}), getattr(response, "usage", None))
        stats = self._last_strip_stats(response)

        result: dict[str, Any] = {
            "messages": list(self._session.messages),
            "iterations": state["iterations"] + 1,
            "usage": usage,
            "output_stats": self._merge_stats(state.get("output_stats", {}), stats),
        }
        # 用 state 的一个内部标记记录"本轮是否为最终回答"，供条件边读取。
        # 不放进 TypedDict 的公开字段，避免污染 A/B 对比状态。
        result["_has_tool_calls"] = bool(response.has_tool_calls)
        return result

    def strip_reasoning_node(self, state: AgentState) -> dict[str, Any]:
        """独立节点：对最终回答剥离思考块，产出 final_answer。

        对应手写版：
            clean, stats = strip_reasoning(response.content or "")
            if stats.empty_after_strip: 保留原文 else: 用 clean
        """
        messages = list(state["messages"])
        if not messages:
            return {"final_answer": ""}
        last = dict(messages[-1])
        raw = str(last.get("content") or "")
        clean, stats = strip_reasoning(raw)
        content = raw if stats.empty_after_strip else clean
        last["content"] = content
        messages[-1] = last
        self._session.messages = messages

        out_stats = self._merge_stats(state.get("output_stats", {}), stats)
        return {
            "messages": messages,
            "final_answer": content,
            "output_stats": out_stats,
        }

    def execute_tools(self, state: AgentState) -> dict[str, Any]:
        """节点：执行最后一条 assistant 消息里的全部 tool_calls。

        对应手写版 for tc in response.tool_calls 段。工具调用前的
        content 清洗在这里就地做（复用 _clean_message_for_history），
        因为它属于"入历史前的清洗"，不是最终回答的剥离节点。
        """
        last = dict(state["messages"][-1])
        # 工具调用阶段的思考块清洗：与手写版一致，避免下轮发散
        if last.get("content"):
            cleaned, stats = strip_reasoning(str(last["content"]))
            if not stats.empty_after_strip:
                last["content"] = cleaned
        new_messages = list(state["messages"][:-1]) + [last]

        for tc in last.get("tool_calls") or []:
            name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
            args = tc.get("arguments", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
            call_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
            result = self._loop._execute_runtime_tool(name, args)
            new_messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": result,
            })
        self._session.messages = new_messages
        return {"messages": new_messages, "_has_tool_calls": False}

    def should_continue(self, state: AgentState) -> str:
        """条件边：最终回答 -> strip；达上限 -> guard；有工具 -> tools。"""
        if state.get("_has_tool_calls") is False:
            # call_model 刚产出无工具调用的回答，先过剥离节点
            return "strip"
        if state["iterations"] >= state["max_iterations"]:
            return "guard"
        return "tools"

    def guard_max_iter(self, state: AgentState) -> dict[str, Any]:
        """节点：达最大迭代时的兜底回答（对应手写版末尾的警告文本）。"""
        warning = "[警告] 已达最大迭代次数，请检查任务是否过于复杂。"
        return {"final_answer": warning}

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
    def _last_strip_stats(response: Any) -> ReasoningStats | None:
        raw = getattr(response, "content", "") or ""
        _, stats = strip_reasoning(raw)
        return stats

    # ------------------------------------------------------------------
    # 主接口
    # ------------------------------------------------------------------

    def run(self, user_input: str, thread_id: str = "default") -> str:
        """执行一轮（阻塞，非流式），返回最终回答。

        Args:
            user_input: 用户消息
            thread_id: 会话标识。同一 thread_id 下的多次调用会通过
                       checkpointer 续接历史（对应手写版 Session）。
        """
        self.last_usage = {}
        self.last_output_stats = {}
        config = {"configurable": {"thread_id": thread_id}}

        # 从 checkpointer 读取该 thread 的历史快照；没有则用 session 的历史。
        # 覆盖式 state 下显式合并，避免 add_messages 对 dict 消息的 id 要求。
        try:
            snapshot = self.graph.get_state(config)
            prior = list(snapshot.values.get("messages", [])) if snapshot.values else []
        except Exception:
            prior = []
        if not prior:
            prior = list(self._session.messages)
        messages = prior + [{"role": "user", "content": user_input}]

        initial: dict[str, Any] = {
            "messages": messages,
            "iterations": 0,
            "max_iterations": self._config.max_iterations,
            "tool_schemas": self._tool_schemas,
            "final_answer": "",
            "usage": {},
            "output_stats": {},
            "_has_tool_calls": True,  # 首轮必进 call_model -> should_continue 判断
        }
        result = self.graph.invoke(initial, config=config)
        self.last_usage = result.get("usage", {})
        self.last_output_stats = result.get("output_stats", {})
        self._session.messages = list(result["messages"])
        return str(result.get("final_answer", ""))


def build_graph(agent: LangGraphAgent, checkpointer: Any = None) -> Any:
    """构建 StateGraph：START -> call_model -> {strip | tools | guard}。"""
    builder = StateGraph(AgentState)
    builder.add_node("call_model", agent.call_model)
    builder.add_node("strip_reasoning", agent.strip_reasoning_node)
    builder.add_node("execute_tools", agent.execute_tools)
    builder.add_node("guard_max_iter", agent.guard_max_iter)

    builder.add_edge(START, "call_model")
    builder.add_conditional_edges(
        "call_model",
        agent.should_continue,
        {"strip": "strip_reasoning", "tools": "execute_tools", "guard": "guard_max_iter"},
    )
    builder.add_edge("execute_tools", "call_model")
    builder.add_edge("strip_reasoning", END)
    builder.add_edge("guard_max_iter", END)
    return builder.compile(checkpointer=checkpointer)
