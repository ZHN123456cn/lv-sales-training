# 手写 ReAct vs LangGraph — A/B 对比报告

> 同一输入、同一 prompt、同一工具集、同一 mock LLM 脚本；唯一变量是控制流实现。
> 判定：两套引擎各跑同一黄金 case 集，质量用契约式检查（非空/关键内容/思考块不泄漏/工具结果入历史）。

## 汇总

| 维度 | 手写 ReAct (`agent/loop.py`) | LangGraph (`agent/langgraph_impl/`) | 差异 |
|---|---|---|---|
| 质量通过 | 3/3 | 3/3 | 一致 |
| 平均延迟 | 3.44 ms | 11.59 ms | +8.15 ms |
| Prompt Token | 40 | 40 | 一致 |
| Completion Token | 20 | 20 | 一致 |
| LLM 调用次数 | 4 | 4 | — |

## 逐 case 明细

| case | 手写 | LangGraph | 延迟差(ms) |
|---|---|---|---|
| plain_answer | ✓ | ✓ | +14.48 |
| strip_thinking | ✓ | ✓ | +5.84 |
| with_tool_call | ✓ | ✓ | +4.13 |

## 结论（面试口径）

- 质量：两套引擎在同一黄金集上判定一致——说明 LangGraph 复刻没有改变行为语义。
- 延迟：LangGraph 略高 8.15 ms，来自框架的图调度开销（mock 模式下主要是节点流转成本）。
- Token：完全一致——prompt/工具 schema 复用手写版组件，理论上应相等。
- 工程取舍：LangGraph 把循环/条件边/checkpointer 显式化，可读性和状态可回溯更好；手写版少一层调度，延迟更低。两者质量一致时，选框架还是手写取决于团队对「可维护性 vs 极致延迟」的权衡。

## 复现

```bash
python -m agent.langgraph_impl.ab_compare            # mock 模式（本报告）
python -m agent.langgraph_impl.ab_compare --live     # 真实 API 模式
```
