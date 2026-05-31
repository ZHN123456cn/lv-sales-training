# 多 Agent 设计文档

## 背景

当前项目是一个简化版 agent，已经支持 CLI、LLM、tool、skill、memory、MCP。下一步要支持多个 agent。

本设计采用“目录即 agent”的模型：每个 agent 拥有独立 workspace，workspace 同时是该 agent 的工作目录和状态目录。切换 agent 后，工具读写文件、shell 执行、skill 加载、memory、session、MCP 相对 cwd 都切换到该 agent 的 workspace。

## 已确认决策

### Agent 目录命名

默认 agent 名称为 `main`。

目录规则：

```text
main          -> workspace/
work          -> workspace_work/
<agent_name>  -> workspace_<agent_name>/
```

`main` 使用特殊目录名 `workspace/`，不使用 `workspace_main/`。

### Agent 目录结构

每个 agent workspace 固定结构如下：

```text
workspace[_agent]/
  IDENTITY.md
  MEMORY.md
  memory/
    YYYY-MM-DD.md
  sessions/
  skills/
```

含义：

- `IDENTITY.md`：当前 agent 的人设
- `MEMORY.md`：当前 agent 的长期记忆
- `memory/`：当前 agent 的每日记忆目录
- `sessions/`：当前 agent 的会话历史
- `skills/`：当前 agent 可用 skills

### Workspace 语义

`workspace[_agent]/` 同时承担两个角色：

- agent 状态目录：保存 identity、memory、sessions、skills
- agent 工作目录：切换 agent 后，文件工具、shell、MCP 相对 cwd 都基于该目录

因此，切换到 `work` 后，当前工作目录应变为：

```text
<project_root>/workspace_work/
```

### 启动参数

新增启动参数：

```bash
python main.py --agent work
```

规则：

- 不传 `--agent` 时默认启动 `main`
- 传 `--agent <name>` 时启动指定 agent
- 当前 agent 不写回配置文件，也不写状态文件
- `/agent <name>` 只影响当前进程

启动不存在的 agent 时：

- 交互模式：询问用户是否创建 workspace
- 非交互模式，例如 `--message`：直接报错退出

### CLI 斜杠命令

新增命令：

```text
/agent list
/agent <name>
/agent create <name>
```

行为：

- `/agent list`：扫描项目根目录下的 `workspace/` 和 `workspace_*`，列出已有 agent
- `/agent <name>`：切换到已存在 agent
- `/agent create <name>`：创建 agent 目录结构，但不自动切换

`/agent list` 输出内容：

- agent 名
- 是否当前 agent
- workspace 路径

`/agent <name>` 不隐式创建目录。如果目标 workspace 不存在，直接报错，提示使用 `/agent create <name>`。

### Session 行为

session 完全按 agent 隔离。

目录：

```text
workspace/sessions/
workspace_work/sessions/
```

切换 agent 时：

1. 保存当前 agent 的当前 session
2. 关闭当前 `AgentLoop`
3. 切换到目标 agent workspace
4. 为目标 agent 创建新的 `SessionManager`
5. 加载目标 agent 最近 session
6. 如果目标 agent 没有 session，则创建新 session
7. 重建 LLM loop、tool context、skill registry、memory store、MCP manager

不复用当前 session。否则会把一个 agent 的对话历史泄露给另一个 agent。

### Skill 行为

只加载当前 agent workspace 下的 `skills/`。

示例：

```text
main -> workspace/skills/
work -> workspace_work/skills/
```

不再默认加载项目根目录的 `skills/`。

### Memory 行为

长期记忆：

```text
workspace[_agent]/MEMORY.md
```

每日记忆：

```text
workspace[_agent]/memory/YYYY-MM-DD.md
```

SQLite 派生索引也应放在当前 agent workspace 下，建议：

```text
workspace[_agent]/memory.sqlite3
```

或者继续由 `MarkdownMemoryStore` 根据长期记忆文件父目录派生。

### Identity 行为

`IDENTITY.md` 启动某个 agent 时读取一次。

规则：

- 程序启动 agent 时读取一次
- `/agent <name>` 切换 agent 时重建 loop，因此读取目标 agent 的 `IDENTITY.md`
- 同一个 agent 运行期间手动修改 `IDENTITY.md` 不自动生效
- 修改后要重启，或切换出去再切回来

如果 `IDENTITY.md` 缺失：

- 启动、切换或创建 agent 时自动生成默认 `IDENTITY.md`
- 如果 agent workspace 已存在但内部文件不完整，也自动补齐 `MEMORY.md`、`memory/`、`sessions/`、`skills/`
- 但 `/agent <name>` 不创建整个 workspace；只补齐已存在 workspace 内部结构

### MCP 行为

MCP 配置仍然全局，继续从 `config.yaml` 读取。

但 MCP server 的相对 `cwd` 基于当前 agent workspace 解析。

示例：

```yaml
mcp:
  servers:
    filesystem:
      transport: "stdio"
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
      cwd: "."
```

在 `main` 下：

```text
cwd -> workspace/
```

在 `work` 下：

```text
cwd -> workspace_work/
```

## 参考 OpenClaw 的实现思路

参考源码路径：

```text
C:\hsq\code\github\openclaw\
```

参考时跳过所有 `project_class*` 目录。

OpenClaw 中值得借鉴的是“先解析 agent scope，再由 scope 派生运行时路径”的模式，而不是直接照搬完整 subagent 系统。

相关参考点：

- `src/agents/agent-scope.ts`
  - 解析默认 agent
  - 解析 agent workspace
  - 解析 per-agent 配置
- `src/agents/workspace.ts`
  - 创建 workspace
  - 加载 workspace bootstrap 文件
- `src/agents/workspace-dirs.ts`
  - 列出 agent workspace
- `src/agents/tools/agents-list-tool.ts`
  - 暴露 agent 列表

本项目第一版不实现 OpenClaw 的后台 subagent registry、gateway routing、并发 run manager、agent-to-agent message。先实现本地 CLI 的多 agent 切换和上下文隔离。

## 新增模块设计

建议新增：

```text
agent/agents.py
```

职责：

- 校验 agent 名称
- 标准化 agent 名称
- 根据 agent 名称解析 workspace 路径
- 创建 agent workspace 结构
- 补齐缺失的内部文件
- 扫描已有 agent
- 提供 `AgentScope`

建议数据结构：

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class AgentScope:
    id: str
    workspace_dir: Path
    identity_path: Path
    memory_path: Path
    daily_memory_dir: Path
    session_dir: Path
    skills_dir: Path
```

路径派生：

```python
def workspace_dir_for(project_root: Path, agent_id: str) -> Path:
    if agent_id == "main":
        return project_root / "workspace"
    return project_root / f"workspace_{agent_id}"
```

agent 名称校验：

- 只允许小写字母、数字、`_`、`-`
- 输入可转小写
- 禁止空字符串
- 禁止 `.`、`..`、路径分隔符、Windows 盘符

## 配置设计

新增配置段：

```yaml
agents:
  default: main
```

不需要 `active`，因为当前 agent 不持久化。

新增环境变量可选支持：

```text
AGENT_ID=work
```

解析优先级：

1. CLI `--agent <name>`
2. 环境变量 `AGENT_ID`
3. `agents.default`
4. `main`

`AgentConfig` 建议增加：

```python
@dataclass
class AgentsConfig:
    default: str = "main"
```

以及运行时字段：

```python
agent_scope: AgentScope | None = None
```

## 现有路径解析调整

当前 `AgentConfig` 已有这些方法：

- `resolved_work_dir()`
- `resolved_session_dir()`
- `resolved_skill_dirs()`
- `resolved_memory_dir()`
- `resolved_memory_file()`
- `resolved_mcp_cwd()`

多 agent 后建议调整为：

- `resolved_work_dir()` 返回当前 `agent_scope.workspace_dir`
- `resolved_session_dir()` 返回当前 `agent_scope.session_dir`
- `resolved_skill_dirs()` 返回 `[agent_scope.skills_dir]`
- `resolved_memory_dir()` 返回当前 `agent_scope.workspace_dir`
- `resolved_memory_file()` 返回当前 `agent_scope.memory_path`
- `resolved_mcp_cwd(server)` 中相对路径基于当前 `agent_scope.workspace_dir`

这样 `memory.py`、`skills.py`、`session.py`、`mcp.py` 的主体逻辑可以少改，主要依赖 config 的路径解析结果。

## ContextManager 调整

`ContextManager` 应增加启动时缓存的 identity 文本。

建议 `AgentLoop` 构造时读取：

```python
identity_text = read_identity(config.agent_scope)
```

然后传给 `ContextManager`。

system prompt 中注入：

```text
Agent ID: work
Agent workspace: C:\...\workspace_work

<identity>
...
</identity>
```

identity 应在 skill 和 memory 前注入，因为它定义当前 agent 的基础行为。

## CLI 调整

### 启动参数

`main.py` 新增：

```python
parser.add_argument("--agent", type=str, default=None)
```

启动流程调整：

1. 加载 config
2. 解析 agent id
3. 解析 agent scope
4. 如果 workspace 不存在：
   - 交互模式询问是否创建
   - 非交互模式报错退出
5. 补齐 workspace 内部结构
6. 初始化 `SessionManager(config.resolved_session_dir())`
7. 初始化 skill、memory、loop

### `/agent list`

扫描：

- `workspace/` -> `main`
- `workspace_*` -> 去掉前缀后的名字

只列出实际存在的目录。

### `/agent create <name>`

创建：

```text
workspace_<name>/
  IDENTITY.md
  MEMORY.md
  memory/
  sessions/
  skills/
```

如果目录已存在，补齐内部结构并提示已存在。

不自动切换。

### `/agent <name>`

切换流程：

1. 校验 agent 名称
2. 检查 workspace 是否存在
3. 不存在则报错
4. 保存当前 session
5. 关闭当前 loop
6. 更新 config 的 `agent_scope`
7. 补齐目标 workspace 内部结构
8. 重建 `SessionManager`
9. 加载目标 agent 最近 session，没有则创建
10. 重建 skill registry 和 loop
11. 打印当前 agent、workspace、session

## 迁移设计

运行时不做迁移。

迁移由单独脚本完成：

```text
scripts/migrate_main_workspace.py
```

迁移目标：

```text
.sessions/              -> workspace/sessions/
skills/                 -> workspace/skills/
.agent_memory/MEMORY.md -> workspace/MEMORY.md
.agent_memory/daily/    -> workspace/memory/
```

迁移成功后删除旧目录。

冲突策略：

- 如果目标路径已存在且非空，脚本停止
- 不覆盖
- 不合并
- 不删除旧数据
- 用户手动处理后重新运行脚本

脚本应先执行完整预检，再执行移动，避免移动一半后失败。

建议支持：

```bash
python scripts/migrate_main_workspace.py --dry-run
python scripts/migrate_main_workspace.py
```

## 测试计划

新增 `tests/test_agents.py`：

- `main` 解析到 `workspace/`
- `work` 解析到 `workspace_work/`
- 非法 agent 名称被拒绝
- `create_agent_workspace()` 创建完整结构
- 已存在 workspace 缺少内部文件时会补齐
- `list_agents()` 能识别 `workspace/` 和 `workspace_*`

更新 `tests/test_config.py`：

- `--agent` 或解析后的 agent id 会影响 runtime scope
- `resolved_work_dir()` 指向当前 workspace
- `resolved_session_dir()` 指向当前 workspace 的 `sessions/`
- `resolved_skill_dirs()` 只返回当前 workspace 的 `skills/`
- `resolved_memory_file()` 指向当前 workspace 的 `MEMORY.md`
- MCP 相对 cwd 基于当前 workspace

更新 `tests/test_context.py`：

- system prompt 包含 Agent ID
- system prompt 包含 Agent workspace
- system prompt 包含启动时读取的 identity

更新 `tests/test_memory.py`：

- 不同 agent 的 memory 文件互不影响
- 每日记忆写入 `memory/YYYY-MM-DD.md`

更新 CLI 相关测试：

- `/agent list` 输出已有 agent
- `/agent create work` 创建但不切换
- `/agent work` 切换到已有 agent
- `/agent missing` 报错
- 切换 agent 会保存当前 session，并加载目标 agent 最近 session

新增迁移脚本测试：

- dry-run 不移动文件
- 正常迁移后旧目录被删除
- 目标存在且非空时失败
- 预检失败时不移动任何文件

## 实施顺序

1. 新增 `agent/agents.py`
2. 扩展 config，加入 `AgentsConfig` 和 `agent_scope`
3. 调整所有路径解析方法，使其基于当前 agent workspace
4. 修改 `main.py`，支持 `--agent`
5. 修改 `ContextManager`，注入启动时读取的 identity
6. 修改 CLI，支持 `/agent list`、`/agent create <name>`、`/agent <name>`
7. 调整 memory 默认 daily 目录为 `memory`
8. 调整 skills 默认目录为当前 workspace 的 `skills/`
9. 调整 MCP 相对 cwd 解析
10. 新增迁移脚本 `scripts/migrate_main_workspace.py`
11. 补测试
12. 更新 `config.example.yaml` 和相关说明文档

## 第一版不做的事情

第一版不实现：

- 并发 subagent
- agent-to-agent 消息
- LLM 自主切换 agent
- 后台 run registry
- OpenClaw gateway/session key 体系
- 每个 agent 独立 MCP 配置
- 跨 agent memory search
- 全局 skill 与 agent skill 混合加载

这些能力可以后续在当前 agent scope 稳定后再扩展。

## 最终方案摘要

本项目的多 agent 第一版应围绕 `AgentScope` 实现。

启动或切换 agent 时，先解析当前 agent：

```text
agent id -> workspace path -> identity/session/skills/memory/MCP cwd
```

所有运行时组件都从这个 scope 派生路径，而不是各自猜路径。

最终用户体验：

```bash
python main.py
python main.py --agent work
```

CLI 内：

```text
/agent list
/agent create work
/agent work
```

目录上：

```text
workspace/
  IDENTITY.md
  MEMORY.md
  memory/
  sessions/
  skills/

workspace_work/
  IDENTITY.md
  MEMORY.md
  memory/
  sessions/
  skills/
```

这样可以在不引入复杂 subagent 系统的前提下，实现清晰、可手工查看、可测试的多 agent 隔离。
