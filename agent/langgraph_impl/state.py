"""StateGraph 的状态定义。

对照手写版 agent/loop.py：
  - messages       = session.messages（对话历史）
  - iterations     = for iteration in range(max_iter) 的迭代指针
  - tool_schemas   = get_openai_schemas_from_defs(runtime_tools)（一次性算好）
  - final_answer   = 最终回答（无 tool_calls 时产出）
  - usage/stats    = token 与思考块剥离统计，对齐手写版 last_usage/last_output_stats

说明：messages 用"覆盖式"流转（节点返回完整列表），而不是 add_messages reducer。
原因：手写版对 tool_calls 消息有 provider-specific 字段、思考块剥离等定制处理，
覆盖式能让每个节点完全掌控历史，语义与手写版逐字对齐，A/B 对比才公平。
"""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict):
    messages: list[dict[str, Any]]
    iterations: int
    max_iterations: int
    tool_schemas: list[dict[str, Any]]
    final_answer: str
    usage: dict[str, int]
    output_stats: dict[str, Any]
    # 内部标记：call_model 本轮是否产出工具调用。条件边据此分叉到
    # strip_reasoning / execute_tools。不参与 A/B 对比的公开指标。
    _has_tool_calls: bool
