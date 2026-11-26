# LangGraph 复刻：手写 ReAct 的框架化对照实现

本目录是手写 ReAct 引擎（`agent/loop.py`）的 **LangGraph 复刻**，目的不是替换，而是回答一个工程问题：

> 手写懂底层，和用框架工程化，到底差在哪？

同一套业务场景、同一批 prompt、同一组工具、同一份评测 case，唯一变量是**控制流实现**。这样产出的 A/B 结论可归因、可复现。

---

## 为什么不做成新项目

- 新项目两周搭不完业务场景、知识库、评测集
- 脱离原项目就失去了「同一黄金集跑两遍」的前提，A/B 不可信
- 放在原仓库的新目录，**不动任何稳定代码**，零回归风险

---

## 模块映射表

| 手写版 | LangGraph 版 | 文件 |
|---|---|---|
| `loop.py` 的 `for iteration in range(max)` | `StateGraph` + 条件边 + `iterations` 状态 | `graph.py` |
| `LLM.chat_blocking()` | `call_model` 节点 | `graph.py` |
| `strip_reasoning()`（内联在循环里） | `strip_reasoning` **独立节点** | `graph.py` |
| `_execute_runtime_tool()` | `execute_tools` 节点（ToolNode 等价） | `graph.py` |
| `session.messages` 持久化 | `checkpointer`（MemorySaver）+ `thread_id` | `graph.py` |
| `training.py` 的 `start_training/chat/evaluate` | 三 Agent 协作图 + `Command(goto=)` handoff | `training_graph.py` |
| `agents.py` 三角色 workspace 切换 | 按 role 创建 session，`current_role` 状态 | `training_graph.py` |
| `build_customer_system_prompt` / `build_evaluation_prompt` / `reconcile_report_total` | 直接复用，**不重写** | `training_graph.py` |

**关键设计**：LLM、ContextManager、工具注册表、思考块剥离、评价体系全部复用手写版组件。只迁移控制流，保证 A/B 对比时质量差异只来自框架本身。

---

## 状态设计（面试常问）

```python
class AgentState(TypedDict):
    messages: list[dict]        # 对话历史，覆盖式流转（与 session.messages 对齐）
    iterations: int             # 循环指针
    max_iterations: int
    tool_schemas: list[dict]
    final_answer: str
    usage: dict[str, int]       # token 统计
    output_stats: dict          # 思考块剥离统计
    _has_tool_calls: bool       # 内部标记，条件边据此分叉
```

**为什么 `messages` 用覆盖式而不用 `add_messages` reducer**：手写版对 tool_calls 消息有 provider-specific 字段、思考块剥离等定制处理；覆盖式让每个节点完全掌控历史，语义与手写版逐字对齐，A/B 才公平。而 `add_messages` 要求消息带 `id`/`tool_call_id`，会引入与手写版不一致的字段。

**条件边的三路分叉**：
```
call_model ─┬─ 无工具调用 → strip_reasoning → END
            ├─ 有工具调用 → execute_tools → 回到 call_model
            └─ 达最大迭代 → guard_max_iter → END
```

---

## checkpointer vs Store

- **checkpointer**（本目录已实现）：存单个 `thread_id` 的状态快照，对应手写版 `Session`。`MemorySaver` 是内存版，**生产可换成 `SqliteSaver` 做持久化**。
- **Store**（未实现，手写版对应 `memory.py` 的长期记忆）：存跨 thread 的长期事实。手写版 `MarkdownMemoryStore` 有 1068 行、含 FTS5+向量混合检索，直接换 LangGraph Store 会丢失这套检索能力——**这是刻意保留手写组件的原因**。

---

## A/B 对比结果

跑 `python -m agent.langgraph_impl.ab_compare` 生成，详见 [`COMPARISON.md`](COMPARISON.md)。

摘要（mock 模式，可复现）：

| 维度 | 手写 ReAct | LangGraph | 差异 |
|---|---|---|---|
| 质量通过 | 3/3 | 3/3 | 一致 |
| 平均延迟 | 3.44 ms | 11.59 ms | +8.15 ms |
| Token | 40/20 | 40/20 | 完全一致 |

**结论**：质量与 Token 完全一致，LangGraph 延迟略高（图调度开销）。框架省掉的是样板代码，没省掉的是设计决策——State 怎么切、条件边怎么分、checkpointer 和 Store 怎么分层，这些在手写版里已经想清楚，换成框架后原样映射即可。

---

## 测试

```bash
python -m pytest tests/test_langgraph_smoke.py    # 核心循环：5 个 case
python -m pytest tests/test_langgraph_training.py # 三 Agent 协作：2 个 case
python -m pytest -q                               # 全量回归
```

全量基线 217 → 迁移后 227，全部通过，手写版零破坏。

---

## 面试三问

**Q：既然用了 LangGraph，为什么还要手写一遍？**
A：手写是为了搞懂 ReAct 每一环在做什么。复刻后发现 StateGraph 就是我的循环+条件边，ToolNode 就是我的 `_execute_runtime_tool`。框架省掉样板代码，没省设计决策。同一黄金集测下来质量一致、Token 一致，LangGraph 延迟高约 8ms 来自图调度——这个结论只有两边都做过才说得出。

**Q：State 怎么设计？**
A：见上方 `AgentState`。重点是 `messages` 用覆盖式而非 reducer，以及 `_has_tool_calls` 内部标记让条件边分三路，而不是塞进 messages 里污染对话历史。

**Q：checkpointer 和 Store 的区别？**
A：checkpointer 存单个 thread 的状态快照（短期记忆，对应 Session），Store 存跨 thread 的长期记忆（对应 `memory.py`）。本实现用了 MemorySaver；长期记忆刻意保留手写的 MarkdownMemoryStore，因为它有 FTS5+向量混合检索，换 Store 会丢能力。
