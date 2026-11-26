"""
LangGraph 版 Agent 引擎（迁移复刻）。

与手写版 `agent/loop.py` 并行存在，目的不是替换，而是提供同一份业务/评测集上的
A/B 对比：质量（agent_guard 判定）、延迟、Token 用量。

设计原则：
  - 复用现有 LLMClient / ContextManager / 工具注册表，不重复造轮子
  - 只把"循环控制流"从手写 for 循环换成 LangGraph StateGraph
  - 同一对话在两套引擎上跑出的结果可直接对比
"""

from .graph import build_graph, LangGraphAgent

__all__ = ["build_graph", "LangGraphAgent"]
