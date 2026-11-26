"""
Agent Loop：ReAct 核心循环。

流程：
  用户输入
    → 追加 user 消息
    → 循环：
        LLM.chat(messages, tools)
        有 tool_calls → 执行工具 → 追加 tool 结果 → 继续
        无 tool_calls → 最终回答 → 退出循环
    → 返回最终文本
"""

from __future__ import annotations

import json
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Callable

from .config import AgentConfig
from .context import ContextManager
from .llm import LLMClient, LLMResponse
from .mcp import MCPManager
from .memory import MarkdownMemoryStore, build_memory_store
from .reasoning import ReasoningFilter, ReasoningStats, strip_reasoning
from .session import Session
from .skills import SkillRegistry, load_skills
from .tools import TOOL_REGISTRY, ToolContext, ToolDef, execute_tool, get_enabled_tools, get_openai_schemas_from_defs


# ---------------------------------------------------------------------------
# 回调类型（供 CLI 监听工具调用过程）
# ---------------------------------------------------------------------------

@dataclass
class ToolCallEvent:
    name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass
class ToolResultEvent:
    name: str
    call_id: str
    result: str


OnToolCall = Callable[[ToolCallEvent], None]
OnToolResult = Callable[[ToolResultEvent], None]


# ---------------------------------------------------------------------------
# AgentLoop
# ---------------------------------------------------------------------------

class AgentLoop:
    def __init__(
        self,
        config: AgentConfig,
        session: Session,
        llm: LLMClient,
        skill_registry: SkillRegistry | None = None,
        memory_store: MarkdownMemoryStore | None = None,
        context_manager: ContextManager | None = None,
        enabled_tools_override: list[str] | None = None,
    ) -> None:
        self._config = config
        self._session = session
        self._llm = llm
        self._skill_registry = skill_registry or load_skills(config)
        self._memory_store = memory_store if memory_store is not None else build_memory_store(config)
        # 场景级工具白名单（如顾客模拟场景传 [] 彻底禁用工具）。
        # None 表示沿用全局 config；显式传 [] 表示禁用全部工具且不启动 MCP。
        tools_disabled = enabled_tools_override is not None and not enabled_tools_override
        effective_tools = (
            config.enabled_tools if enabled_tools_override is None else enabled_tools_override
        )
        self._enabled_tools = list(effective_tools)
        if self._memory_store is None:
            self._enabled_tools = [
                name for name in self._enabled_tools
                if name not in {"memory_read", "memory_search", "memory_write", "memory_reindex"}
            ]
        self._mcp_manager: MCPManager | None = None
        self._local_tools = get_enabled_tools(self._enabled_tools)
        self._mcp_tools: list[ToolDef] = []
        if config.mcp.enabled and not tools_disabled:
            self._mcp_manager = MCPManager(config)
            self._mcp_manager.start()
            self._mcp_tools = self._mcp_manager.list_tool_defs()
        self._runtime_tools = self._local_tools + self._mcp_tools
        self._context_manager = context_manager or ContextManager(
            config,
            self._skill_registry,
            self._memory_store,
        )
        # ToolContext 持有当前工作目录，change_dir 工具会修改它
        self._tool_ctx = ToolContext(
            work_dir=config.resolved_work_dir(),
            session_id=session.id,
            skill_registry=self._skill_registry,
            memory_store=self._memory_store,
        )
        # 上一轮的思考块剥离统计（F-20261116-02）。质检据此区分
        # "模型不产生思考块"与"产生了但被剥掉"——可见文本上这两者一样。
        self.last_output_stats: dict[str, object] = {}
        # 上一轮的 token 用量：一轮可能跨多次 LLM 调用（ReAct），要累加。
        # 空字典 = provider 没回 usage，与"用了 0 个 token"不是一回事。
        self.last_usage: dict[str, int] = {}

    @property
    def tool_ctx(self) -> ToolContext:
        return self._tool_ctx

    @property
    def skill_registry(self) -> SkillRegistry:
        return self._skill_registry

    @property
    def memory_store(self) -> MarkdownMemoryStore | None:
        return self._memory_store

    @property
    def enabled_tools(self) -> list[str]:
        return list(self._enabled_tools)

    @property
    def runtime_tools(self) -> list[ToolDef]:
        return list(self._runtime_tools)

    @property
    def mcp_tools(self) -> list[ToolDef]:
        return list(self._mcp_tools)

    @property
    def mcp_server_tools(self) -> dict[str, list[Any]]:
        if self._mcp_manager is None:
            return {}
        grouped: dict[str, list[Any]] = {}
        for tool in self._mcp_manager.tools:
            grouped.setdefault(tool.server_name, []).append(tool)
        return grouped

    @property
    def mcp_server_errors(self) -> dict[str, str]:
        if self._mcp_manager is None:
            return {}
        return self._mcp_manager.errors

    def close(self) -> None:
        if self._mcp_manager is not None:
            self._mcp_manager.stop()
            self._mcp_manager = None

    # ------------------------------------------------------------------
    # 初始化 system prompt
    # ------------------------------------------------------------------

    def ensure_system_prompt(self) -> None:
        """兼容旧接口。system prompt 由 ContextManager 临时构建，不写入 Session。"""
        return None

    # ------------------------------------------------------------------
    # 主接口：阻塞
    # ------------------------------------------------------------------

    def run(
        self,
        user_input: str,
        on_tool_call: OnToolCall | None = None,
        on_tool_result: OnToolResult | None = None,
    ) -> str:
        """
        执行一轮 Agent Loop（非流式最终回答）。

        Args:
            user_input:     用户消息文本
            on_tool_call:   工具调用前回调
            on_tool_result: 工具执行后回调

        Returns:
            最终回答文本
        """
        self._append_user(user_input)
        self.last_output_stats = {}
        self.last_usage = {}

        tool_schemas = get_openai_schemas_from_defs(self._runtime_tools)
        max_iter = self._config.max_iterations

        for iteration in range(max_iter):
            messages = self._context_manager.build_messages(self._session, self._tool_ctx)
            # 有工具调用时强制非流式，便于处理 tool_calls 结构
            response = self._llm.chat_blocking(
                messages=messages,
                tools=tool_schemas if tool_schemas else None,
            )
            self._absorb_usage(getattr(response, "usage", None))

            message = response.to_message()

            if not response.has_tool_calls:
                # 最终回答：剥掉思考块再交付
                clean, stats = strip_reasoning(response.content or "")
                if stats.empty_after_strip:
                    # 只有思考、没有答复。空 assistant 消息会让下一轮请求失败，
                    # 因此保留原文，并在 stats 里标记（这是缺陷，不是长度短）。
                    message["content"] = response.content or ""
                else:
                    message["content"] = clean
                self._session.messages.append(message)
                self._absorb_stats(stats)
                return message["content"]

            # 工具调用阶段：content 若也带思考块，同样剥掉再入历史，
            # 否则下一轮模型会照着自己的思维链继续发散。
            if message.get("content"):
                cleaned, stats = strip_reasoning(str(message["content"]))
                if not stats.empty_after_strip:
                    message["content"] = cleaned
                self._absorb_stats(stats)
            self._session.messages.append(message)

            # 处理工具调用
            tool_results = []
            for tc in response.tool_calls:
                if on_tool_call:
                    on_tool_call(ToolCallEvent(
                        name=tc["name"],
                        arguments=tc["arguments"],
                        call_id=tc["id"],
                    ))

                result = self._execute_runtime_tool(tc["name"], tc["arguments"])

                if on_tool_result:
                    on_tool_result(ToolResultEvent(
                        name=tc["name"],
                        call_id=tc["id"],
                        result=result,
                    ))

                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

            self._session.messages.extend(tool_results)

        return "[警告] 已达最大迭代次数，请检查任务是否过于复杂。"

    # ------------------------------------------------------------------
    # 主接口：流式最终回答
    # ------------------------------------------------------------------

    def stream_run(
        self,
        user_input: str,
        on_tool_call: OnToolCall | None = None,
        on_tool_result: OnToolResult | None = None,
    ) -> Generator[str, None, None]:
        """
        执行一轮 Agent Loop，最终回答以流式 yield。
        工具调用阶段依然阻塞（OpenAI API 限制）。
        """
        self._append_user(user_input)
        self.last_output_stats = {}
        self.last_usage = {}

        tool_schemas = get_openai_schemas_from_defs(self._runtime_tools)
        max_iter = self._config.max_iterations

        for iteration in range(max_iter):
            is_last_possible = (iteration == max_iter - 1)
            messages = self._context_manager.build_messages(self._session, self._tool_ctx)

            if is_last_possible:
                # 最后一次迭代强制流式
                response = self._llm.chat(
                    messages=messages,
                    tools=tool_schemas if tool_schemas else None,
                    stream=True,
                )
            else:
                response = self._llm.chat_blocking(
                    messages=messages,
                    tools=tool_schemas if tool_schemas else None,
                )
            self._absorb_usage(getattr(response, "usage", None))

            if not response.has_tool_calls:
                # 最终回答，流式输出。逐 token 过思考块过滤器：
                # 标签可能被切在两个 token 里（"<thi" + "nk>"），所以必须
                # 由同一个状态机跨 token 持有未决尾巴。
                guard = ReasoningFilter()
                clean_parts: list[str] = []
                raw_parts: list[str] = []
                for token in response.stream_text():
                    raw_parts.append(token)
                    visible = guard.feed(token)
                    if visible:
                        clean_parts.append(visible)
                        yield visible
                tail = guard.flush()
                if tail:
                    clean_parts.append(tail)
                    yield tail
                # 将完整回答写入 messages，同时保留 provider-specific 字段。
                message = response.to_message()
                clean = "".join(clean_parts)
                message["content"] = clean or "".join(raw_parts)
                self._session.messages.append(message)
                self._absorb_stats(guard.stats)
                return

            # 工具调用阶段（不流式）
            message = response.to_message()
            if message.get("content"):
                cleaned, stats = strip_reasoning(str(message["content"]))
                if not stats.empty_after_strip:
                    message["content"] = cleaned
                self._absorb_stats(stats)
            self._session.messages.append(message)

            tool_results = []
            for tc in response.tool_calls:
                if on_tool_call:
                    on_tool_call(ToolCallEvent(
                        name=tc["name"],
                        arguments=tc["arguments"],
                        call_id=tc["id"],
                    ))

                result = self._execute_runtime_tool(tc["name"], tc["arguments"])

                if on_tool_result:
                    on_tool_result(ToolResultEvent(
                        name=tc["name"],
                        call_id=tc["id"],
                        result=result,
                    ))

                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

            self._session.messages.extend(tool_results)

        yield "[警告] 已达最大迭代次数，请检查任务是否过于复杂。"

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    def _append_user(self, text: str) -> None:
        self._session.messages.append({"role": "user", "content": text})

    def _absorb_usage(self, usage: dict[str, int] | None) -> None:
        """累加 token 用量。provider 不回 usage 时保持空字典（= 没采到）。"""
        if not usage:
            return
        cur = self.last_usage
        cur["prompt"] = int(cur.get("prompt", 0)) + int(usage.get("prompt") or 0)
        cur["completion"] = int(cur.get("completion", 0)) + int(usage.get("completion") or 0)
        cur["calls"] = int(cur.get("calls", 0)) + 1

    def _absorb_stats(self, stats: ReasoningStats) -> None:
        """累加一轮内的剥离统计（一次回答可能跨多个 ReAct 迭代）。"""
        cur = self.last_output_stats
        cur["blocks"] = int(cur.get("blocks", 0)) + stats.blocks
        cur["chars"] = int(cur.get("chars", 0)) + stats.chars
        cur["raw_chars"] = int(cur.get("raw_chars", 0)) + stats.raw_chars
        cur["clean_chars"] = int(cur.get("clean_chars", 0)) + stats.clean_chars
        if stats.empty_after_strip:
            cur["empty_after_strip"] = True

    def _execute_runtime_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name in TOOL_REGISTRY:
            return execute_tool(name, arguments, self._tool_ctx)
        if self._mcp_manager is not None:
            return self._mcp_manager.call_tool(name, arguments)
        return execute_tool(name, arguments, self._tool_ctx)
