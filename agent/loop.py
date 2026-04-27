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
from .session import Session
from .skills import SkillRegistry, load_skills
from .tools import ToolContext, execute_tool, get_openai_schemas


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
        context_manager: ContextManager | None = None,
    ) -> None:
        self._config = config
        self._session = session
        self._llm = llm
        self._skill_registry = skill_registry or load_skills(config)
        self._context_manager = context_manager or ContextManager(config, self._skill_registry)
        # ToolContext 持有当前工作目录，change_dir 工具会修改它
        self._tool_ctx = ToolContext(
            work_dir=config.resolved_work_dir(),
            session_id=session.id,
            skill_registry=self._skill_registry,
        )

    @property
    def tool_ctx(self) -> ToolContext:
        return self._tool_ctx

    @property
    def skill_registry(self) -> SkillRegistry:
        return self._skill_registry

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

        tool_schemas = get_openai_schemas(self._config.enabled_tools)
        max_iter = self._config.max_iterations

        for iteration in range(max_iter):
            messages = self._context_manager.build_messages(self._session, self._tool_ctx)
            # 有工具调用时强制非流式，便于处理 tool_calls 结构
            response = self._llm.chat_blocking(
                messages=messages,
                tools=tool_schemas if tool_schemas else None,
            )

            self._session.messages.append(response.to_message())

            if not response.has_tool_calls:
                # 最终回答
                return response.content

            # 处理工具调用
            tool_results = []
            for tc in response.tool_calls:
                if on_tool_call:
                    on_tool_call(ToolCallEvent(
                        name=tc["name"],
                        arguments=tc["arguments"],
                        call_id=tc["id"],
                    ))

                result = execute_tool(tc["name"], tc["arguments"], self._tool_ctx)

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

        tool_schemas = get_openai_schemas(self._config.enabled_tools)
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

            if not response.has_tool_calls:
                # 最终回答，流式输出
                full_text_parts: list[str] = []
                for token in response.stream_text():
                    full_text_parts.append(token)
                    yield token
                # 将完整回答写入 messages
                self._session.messages.append({
                    "role": "assistant",
                    "content": "".join(full_text_parts),
                })
                return

            # 工具调用阶段（不流式）
            self._session.messages.append(response.to_message())

            tool_results = []
            for tc in response.tool_calls:
                if on_tool_call:
                    on_tool_call(ToolCallEvent(
                        name=tc["name"],
                        arguments=tc["arguments"],
                        call_id=tc["id"],
                    ))

                result = execute_tool(tc["name"], tc["arguments"], self._tool_ctx)

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
