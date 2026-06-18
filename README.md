# Hello World MCP Demo

A minimal Python MCP server and client. The server exposes one tool:
`hello_world`.

## Layout

```text
servermcp/
  server.py
  requirements.txt
clientmcp/
  client.py
```

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r servermcp\requirements.txt
```

If you are running in a Linux environment, use:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r servermcp/requirements.txt
```

## Run Server

```powershell
.\.venv\Scripts\python.exe servermcp\server.py
```

## Run Client

```powershell
.\.venv\Scripts\python.exe clientmcp\client.py
```

The client starts the server through stdio, lists available tools, and calls
`hello_world`.

Expected output:

```text
available tools: ['hello_world']
hello_world result: Hello, world!
```

## MCP Client Config

OpenClaw supports MCP stdio servers. If the `python` command used by OpenClaw
already has the `mcp` package installed, this configuration is enough:

```json
{
  "mcpServers": {
    "hello-world": {
      "command": "python",
      "args": [
        "/home/node/.openclaw/workspace/mcptest/servermcp/server.py"
      ]
    }
  }
}
```

You can verify the `python` environment with:

```bash
python -c "from mcp.server.fastmcp import FastMCP; print('ok')"
```

If that command fails with `ModuleNotFoundError: No module named 'mcp'`, point
OpenClaw to the virtual environment Python instead:

```json
{
  "mcpServers": {
    "hello-world": {
      "command": "/home/node/.openclaw/workspace/mcptest/.venv/bin/python",
      "args": [
        "/home/node/.openclaw/workspace/mcptest/servermcp/server.py"
      ],
      "cwd": "/home/node/.openclaw/workspace/mcptest"
    }
  }
}
```

On Windows, use the virtual environment Python path:

```json
{
  "mcpServers": {
    "hello-world": {
      "command": "C:\\Users\\hh\\.openclaw\\workspace\\mcptest\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\Users\\hh\\.openclaw\\workspace\\mcptest\\servermcp\\server.py"
      ],
      "cwd": "C:\\Users\\hh\\.openclaw\\workspace\\mcptest"
    }
  }
}
```

---

# LV 销售培训 Agent（RAG 版）

本项目包含一个基于 LLM 的通用 Agent 框架，以及 LV 销售培训垂直场景，支持多种 RAG 检索策略。

## 项目结构

```
project_class_tool_skill_memoryplus_mcp_agents/
├── agent/                      # Agent 核心代码
├── mcp/                        # MCP Server
│   └── lv_training/           # LV 销售培训 Server
├── rag/                        # RAG 检索系统
│   ├── embed/                 # Embedding 抽象层（ollama/qwen）
│   ├── utils/                 # 工具（chunker、RRF）
│   ├── grep_server.py         # 关键字上下文搜索
│   ├── dirindex_server.py     # 目录索引搜索
│   ├── fts5_server.py        # FTS5 全文搜索
│   └── vector_server.py       # 向量检索
├── workspace/
│   ├── kg/                   # 知识库
│   │   ├── index.md          # 目录索引
│   │   ├── lv/               # LV 品牌知识
│   │   ├── sales/            # 销售知识
│   │   └── evaluation/        # 点评标准
│   └── skills/               # Agent Skills
├── rag_config.yaml            # RAG 配置文件
└── config.yaml                 # Agent 配置（含 MCP Servers）
```

## 快速开始

### 1. 安装依赖

```powershell
pip install -r project_class_tool_skill_memoryplus_mcp_agents/requirements.txt
```

### 2. 启动 Agent

```powershell
.venv\Scripts\python.exe project_class_tool_skill_memoryplus_mcp_agents\main.py
```

### 3. 启动销售培训（交互模式）

```powershell
.venv\Scripts\python.exe project_class_tool_skill_memoryplus_mcp_agents\mcp\lv_training\training_client.py
```

### 4. Coach 点评

```powershell
.venv\Scripts\python.exe project_class_tool_skill_memoryplus_mcp_agents\mcp\lv_training\coach_client.py
```

---

## RAG 系统

### 架构概览

RAG 系统由 4 个独立的 MCP Server 组成，通过 `config.yaml` 的 `mcp.servers` 配置节接入 Agent：

| Server | 工具 | 用途 |
|--------|------|------|
| `rag-grep` | `grep_search` | 关键字上下文搜索，支持 LLM 迭代优化 |
| `rag-dirindex` | `dirindex_search` | 通过目录索引文件定位相关文档 |
| `rag-fts5` | `fts5_search` | SQLite FTS5 关键词全文搜索 |
| `rag-vector` | `vector_search` | Embedding 语义相似性搜索 |

### 索引重建命令

首次使用前需要重建索引：

```powershell
# FTS5 全文索引
.venv\Scripts\python.exe -m rag.fts5_server --rebuild

# 向量索引（需要 ollama 或 qwen embedding 服务）
.venv\Scripts\python.exe -m rag.vector_server --rebuild

# 同步目录索引（将 kg/ 下文档追加到 index.md）
.venv\Scripts\python.exe -m rag.dirindex_server --sync
```

### RAG 工具使用

#### grep_search（关键字上下文搜索）

```python
# 基础搜索
grep_search(keywords=["路易威登", "品牌历史"], context_chars=800)

# 只搜索指定文档
grep_search(keywords=["工艺"], doc_filter=["lv/brand.md", "lv/craft.md"])

# 扩展上下文字符数
grep_search(keywords=["皮质"], context_chars=1500)
```

#### dirindex_search（目录索引搜索）

```python
# 根据用户问题返回相关文档列表
dirindex_search(query="路易威登品牌历史和工艺是什么")

# 只在指定文档中分析
dirindex_search(query="产品推荐", doc_filter=["lv/products.md"])
```

#### fts5_search（FTS5 全文搜索）

```python
# 关键词搜索
fts5_search(query="品牌历史 工艺", top_k=5)

# 只搜索指定文档
fts5_search(query="VIP服务", doc_filter=["lv/service.md"])
```

#### vector_search（向量语义搜索）

```python
# 语义搜索
vector_search(query="品牌故事和工艺介绍", top_k=5)
```

### 配置修改

编辑 `rag_config.yaml`：

```yaml
# Embedding provider: "ollama" 或 "qwen"
vector:
  embedding:
    provider: "ollama"
    ollama:
      base_url: "http://localhost:11434"
      model: "qwen3-embedding:0.6b"

# 知识库根目录
global:
  kg_root: "workspace/kg"
```

### 知识库结构

```
workspace/kg/
├── index.md                    # 目录索引（Markdown 标题树）
├── lv/
│   ├── brand.md               # LV品牌历史
│   ├── products.md            # 产品线
│   └── service.md             # 服务礼仪
├── sales/
│   ├── skills.md             # 需求挖掘
│   ├── objection.md          # 异议处理
│   ├── gift.md               # 送礼推荐
│   └── scripts.md            # 话术脚本
└── evaluation/
    └── criteria.md           # 评分标准
```

---

## MCP Server 手动测试

```powershell
# 启动 Grep Server（stdio 模式）
.venv\Scripts\python.exe -m rag.grep_server

# 启动 FTS5 Server
.venv\Scripts\python.exe -m rag.fts5_server

# 启动 Vector Server
.venv\Scripts\python.exe -m rag.vector_server

# 启动 Dirindex Server
.venv\Scripts\python.exe -m rag.dirindex_server
```
