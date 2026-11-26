# LV 销售培训 Agent — 多智能体协作 + RAG + 质检评测

一个从零手写的 LLM Agent 系统：自研 ReAct 引擎驱动**顾客模拟 / 销售训练 / 教练评估**三 Agent 协作，4 路检索 + RRF 融合的 RAG 知识库，MCP 工具接入（stdio / 子进程 / HTTP 三种传输），并配套一套可复现的**评测与质检流程**。另用 LangGraph 复刻同一引擎做 A/B 对比，验证"手写懂底层 + 框架能工程化"两种能力。

> 项目重点不只是"让 Agent 跑起来"，而是"验证它到底做得好不好"——这是本仓库最想展示的能力。

## 亮点（3 秒版）

- **手写 ReAct 引擎**：工具调用、多轮规划、会话与记忆管理均自行实现（见 `agent/loop.py`、`agent/memory.py`）；**另用 LangGraph 复刻同一引擎**，同一黄金集做 A/B 对比（见下）
- **三 Agent 协作**：顾客模拟 → 销售训练 → 教练点评，职责拆分 + 契约化交接（见 `backend/routers/training.py`、`agent/langgraph_impl/training_graph.py`）
- **RAG 4 路检索**：grep / 目录索引 / SQLite FTS5 / 向量检索，RRF 融合
- **MCP 三协议**：同进程 / 子进程 / HTTP，可插拔接入（`agent/mcp.py`、`clientmcp/`、`servermcp/`）
- **可验证**：自动化测试套件 + 缺陷分析报告（见下）；真实会话记录本地留存，仓库不含
- **完整技术文档**：`DESIGN.md`、`agents_design.md`、`mcp_design.md`、`项目说明.md`

## 评测与质检（重点）

本项目把"输出好不好"做成了可复现的工程流程，而非主观判断：

| 环节 | 实现 | 产出物 |
|---|---|---|
| 评测集 | 真实业务场景构造的测试集 | `tests/`（pytest） |
| Bad case 分析 | 定位到具体对话轮次原文，归类、复现、回流 | 缺陷分析报告 |
| 回归验证 | 每次改动跑全量测试，对比前后基线 | 自动化测试套件 |
| 知识库治理 | 文档清洗、切分策略、4 路检索效果对比 | RAG 检索模块 |

配套独立质检框架 **agent_guard**（另仓）：YAML 契约 + 黄金集 + 沙箱隔离，判定引擎 P/R/F1 = 1.0，产出数百份质检报告与 13 份缺陷分析报告。

## 手写 ReAct vs LangGraph（A/B 对比）

用同一套黄金 case、同一 prompt、同一工具集，**唯一变量是控制流实现**，把自研 ReAct 循环复刻成 LangGraph 状态图（见 `agent/langgraph_impl/`）。不是"换了框架重写一遍"，而是同一引擎的两种实现做可复现对比。

| 维度 | 手写 ReAct (`agent/loop.py`) | LangGraph (`agent/langgraph_impl/`) | 差异 |
|---|---|---|---|
| 质量通过（契约式检查） | 3/3 | 3/3 | 一致 |
| Prompt / Completion Token | 40 / 20 | 40 / 20 | 一致 |
| 平均延迟 | 3.44 ms | 11.59 ms | +8.15 ms（图调度开销） |
| 控制流形态 | 380 行 for 循环 | 4 节点图 + 条件边 + checkpointer | 状态可回溯、可插拔 |

**结论**：质量与 Token 完全一致，证明复刻没有改变行为语义；延迟差来自框架调度，诚实呈现而非隐藏。手写版少一层调度、延迟更低；LangGraph 把循环/记忆/角色交接显式化，可读性与可维护性更好。两者取舍取决于团队对「极致延迟 vs 工程化」的权衡。

复现（完整数据与逐 case 明细见 [`agent/langgraph_impl/COMPARISON.md`](agent/langgraph_impl/COMPARISON.md)）：

```bash
python -m agent.langgraph_impl.ab_compare            # mock 模式，零成本可复现
python -m agent.langgraph_impl.ab_compare --live     # 真实 API 模式
python -m agent.langgraph_impl.smoke_interactive     # 交互式手动对比
```

## 技术栈

- **后端**：Python 3.10+ / FastAPI（15 个 API）/ SQLite（FTS5）
- **前端**：Vue 3
- **Agent**：自研 ReAct 循环 + Function Calling 式工具调用 + Skill 管理；LangGraph 复刻版（A/B 对照，非生产路径）
- **RAG**：自研 4 路检索（grep / dirindex / FTS5 / vector）+ jieba 分词 + RRF 融合
- **工具接入**：MCP，支持 stdio / 子进程 / HTTP 三种传输
- **测试**：pytest

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env       # 填入你的 API Key
python main.py             # 启动 Agent
python run_backend.py      # 后端 API
python run_frontend.py     # 前端
```

> 配置文件见 `config.example.yaml`；`config.yaml` 与 `.env` 已 gitignore，仓库内不含任何密钥。

## 目录结构

```
.
├── agent/              # Agent 核心（ReAct 引擎、工具调用、记忆）
│   └── langgraph_impl/ # LangGraph 复刻版 + A/B 对比脚本与报告
├── backend/            # FastAPI 后端
├── frontend/           # Vue 3 前端
├── clientmcp/ servermcp/# MCP 客户端 / 服务端示例
├── project_class_all_ragplus/  # RAG 4 路检索 + RRF
├── workspace/          # 知识库（LV / 销售 / 评分标准）
├── tests/              # 自动化测试套件
├── .sessions/          # 真实会话记录（gitignore，本地留存）
├── DESIGN.md agents_design.md mcp_design.md  # 架构与设计文档
└── 项目说明.md
```

## RAG 系统

4 个独立 MCP Server 通过 `config.yaml` 的 `mcp.servers` 接入 Agent：

| Server | 工具 | 用途 |
|---|---|---|
| `rag-grep` | `grep_search` | 关键字上下文搜索，支持 LLM 迭代优化 |
| `rag-dirindex` | `dirindex_search` | 目录索引定位相关文档 |
| `rag-fts5` | `fts5_search` | SQLite FTS5 全文检索 |
| `rag-vector` | `vector_search` | Embedding 语义相似度检索 |

索引重建：

```bash
python -m rag.fts5_server --rebuild
python -m rag.vector_server --rebuild
python -m rag.dirindex_server --sync
```

详细用法见 [`项目说明.md`](项目说明.md) 与 [`DESIGN.md`](DESIGN.md)。
