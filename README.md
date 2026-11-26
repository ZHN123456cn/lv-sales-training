# LV 销售培训 Agent — 多智能体协作 + RAG + 质检评测

一个从零手写的 LLM Agent 系统：自研 ReAct 引擎驱动**顾客模拟 / 销售训练 / 教练评估**三 Agent 协作，4 路检索 + RRF 融合的 RAG 知识库，MCP 工具接入（stdio / 子进程 / HTTP 三种传输），并配套一套可复现的**评测与质检流程**。

> 项目重点不只是"让 Agent 跑起来"，而是"验证它到底做得好不好"——这是本仓库最想展示的能力。

## 亮点（3 秒版）

- **手写 ReAct 引擎**，非 LangChain：工具调用、多轮规划、会话与记忆管理均自行实现（见 `agent/loop.py`、`agent/memory.py`、`agent/skills.py`）
- **三 Agent 协作**：顾客模拟 → 销售训练 → 教练点评，职责拆分 + 契约化交接（`agent/agents.py`）
- **RAG 4 路检索**：grep / 目录索引 / SQLite FTS5 / 向量检索，RRF 融合
- **MCP 三协议**：同进程 / 子进程 / HTTP，可插拔接入（`agent/mcp.py`、`clientmcp/`、`servermcp/`）
- **可验证**：10 个测试文件组成的自动化测试套件 + 缺陷分析报告
- **完整技术文档**：`DESIGN.md`、`agents_design.md`、`mcp_design.md`、`项目说明.md`

## 评测与质检（重点）

本项目把"输出好不好"做成了可复现的工程流程，而非主观判断：

| 环节 | 实现 | 产出物 |
|---|---|---|
| 评测集 | 真实业务场景构造的测试集 + YAML 契约定义评分口径 | `tests/`、`config.yaml` |
| Bad case 分析 | 定位到具体对话轮次原文，归类、复现、回流 | 缺陷分析报告 |
| 回归验证 | 每次改动跑全量测试，对比前后基线 | `tests/`（pytest） |
| 知识库治理 | 文档清洗、切分策略、4 路检索效果对比 | `project_class_all_ragplus/` |

配套独立质检框架 **agent_guard**（另仓）：YAML 契约 + 黄金集 + 沙箱隔离，判定引擎 P/R/F1 = 1.0，产出数百份质检报告与 13 份缺陷分析报告。

## 技术栈

- **后端**：Python 3.10+ / FastAPI / SQLite（FTS5）
- **前端**：Vue 3（`frontend/`）
- **Agent**：自研 ReAct 循环 + Function Calling 式工具调用 + Skill 管理
- **RAG**：自研 4 路检索（grep / dirindex / FTS5 / vector）+ jieba 分词 + RRF 融合
- **工具接入**：MCP，支持 stdio / 子进程 / HTTP 三种传输
- **测试**：pytest（10 个测试文件）

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env       # 填入你的 API Key
python main.py             # 启动 Agent
python run_backend.py      # 后端 API
python run_frontend.py     # 前端
```

> 配置文件见 `config.example.yaml`；`config.yaml` 与 `.env` 中的密钥字段已留空，仓库内不含任何真实密钥。

## 目录结构

```
.
├── agent/              # Agent 核心：ReAct 引擎、工具调用、记忆、Skill、MCP
├── backend/            # 后端 API
├── frontend/           # Vue 3 前端
├── clientmcp/ servermcp/# MCP 客户端 / 服务端示例
├── project_class_all_ragplus/  # RAG 4 路检索 + RRF
├── luxury_sales/       # LV 销售培训场景数据
├── tests/              # 自动化测试套件（10 个测试文件）
├── DESIGN.md agents_design.md mcp_design.md  # 架构与设计文档
└── 项目说明.md
```

## RAG 系统

4 个独立 MCP Server 通过 `config.yaml` 的 `mcp.servers` 配置接入 Agent：

| Server | 工具 | 用途 |
|---|---|---|
| `rag-grep` | `grep_search` | 关键字上下文搜索，支持 LLM 迭代优化 |
| `rag-dirindex` | `dirindex_search` | 目录索引定位相关文档 |
| `rag-fts5` | `fts5_search` | SQLite FTS5 全文检索 |
| `rag-vector` | `vector_search` | Embedding 语义相似度检索 |

详细用法见 [`项目说明.md`](项目说明.md) 与 [`DESIGN.md`](DESIGN.md)。
