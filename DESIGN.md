# Python ReAct Agent 详细设计文档

## 1. 项目概述

本项目在 `project_class/ReAct_agent_loop_tool_schema.py` 的基础上，实现一个功能完整的 Python CLI Agent。

**核心目标**：
- 实现完整的 ReAct Agent Loop（Reasoning + Acting）
- 支持常用文件操作、目录操作等工具
- 支持 AgentSkills 风格的 Skill：上下文只暴露 name/description，按需通过 `skill_read` 读取 `SKILL.md`
- 支持分层记忆（Memory）：以 Markdown 作为真源，长期记忆保存稳定事实/偏好/项目约定，每日记忆保存当天观察；通过 SQLite 内的 FTS5 + sqlite-vec 混合检索、每日记忆晋升和重建索引完成管理
- 提供显式上下文管理，统一组装系统提示词、tool 信息、skill 摘要、memory 内容、对话历史、工具调用结果
- 提供类似 openclaw 的交互式 CLI 界面
- 支持 Session 持久化与恢复（与 Memory 相互独立：Session 是单会话事实历史，Memory 是跨会话长期记忆）
- 通过配置文件统一管理项目设置

**工作目录**：`project_class/`（所有路径默认相对于此目录）

---

## 2. 项目结构

```
project_class/
├── DESIGN.md                  # 本设计文档
├── ReAct_agent_loop_tool_schema.py   # 原始参考代码
├── config.yaml                # 项目配置文件（用户填写）
├── config.example.yaml        # 配置示例（提交到版本库）
├── .env                       # 环境变量（API Key 等，不提交）
├── .sessions/                 # Session 持久化目录
│   ├── index.json             # Session 索引（id -> 元信息）
│   └── <session-id>.json      # 各 Session 的对话历史
├── .agent_memory/             # Memory 根目录（Markdown 为真源，索引为派生缓存）
│   ├── MEMORY.md              # 长期记忆文件，按 scope 分节
│   ├── daily/                 # 每日记忆目录
│   │   └── YYYY-MM-DD.md      # 当天观察、线索、待沉淀内容
│   └── memory.sqlite3         # 同时保存 FTS5 关键词索引与 sqlite-vec 向量索引
├── skills/                    # 工作区 Skills（每个子目录包含 SKILL.md）
│   └── <skill-name>/
│       ├── SKILL.md           # YAML frontmatter + Skill 详细说明
│       ├── references/        # 可选参考资料
│       └── scripts/           # 可选脚本
├── agent/
│   ├── __init__.py
│   ├── cli.py                 # CLI 入口，交互式 REPL
│   ├── context.py             # 上下文管理：构建每次 LLM 请求的 messages
│   ├── loop.py                # Agent Loop（ReAct 核心）
│   ├── llm.py                 # LLM 客户端封装
│   ├── skills.py              # Skill 发现、注册、prompt 摘要与 skill_read 支持
│   ├── memory.py              # MemoryStore + 索引 + memory_read/search/write/reindex
│   ├── tools.py               # 工具注册与执行
│   ├── session.py             # Session 管理
│   └── config.py              # 配置加载
└── main.py                    # 程序入口（python main.py）
```

---

## 3. 配置文件设计

### 3.1 config.yaml

```yaml
# project_class/config.yaml
llm:
  provider: deepseek            # 支持: deepseek | openai | custom
  api_key: ""                   # 也可通过环境变量 AGENT_API_KEY 覆盖
  base_url: "https://api.deepseek.com/v1"
  model: "deepseek-chat"
  temperature: 0.7
  timeout: 60
  max_retries: 3
  streaming: true               # 是否启用流式输出

agent:
  max_iterations: 20            # 单次 Agent Loop 最大步数，防止死循环
  work_dir: "."                 # 工作目录，相对于 project_class/
  system_prompt: |
    你是一个智能助手，可以使用工具帮助用户完成任务。
    工作目录为 {work_dir}。

session:
  dir: ".sessions"              # Session 文件存储目录
  max_history: 100              # 单个 Session 最多保留的消息轮数
  auto_save: true               # 每轮对话后自动保存

tools:
  enabled:                      # 启用的工具列表
    - skill_read
    - memory_read
    - memory_search
    - memory_write
    - memory_reindex
    - read_file
    - write_file
    - list_dir
    - change_dir
    - shell
    - search_files

skills:
  enabled: true                 # 是否启用 Skill 系统
  dirs:                         # Skill 根目录，后者覆盖同名 Skill
    - "skills"                  # 工作区 Skill，默认 project_class/skills
  max_file_bytes: 262144        # 单个 SKILL.md 最大读取字节数
  max_prompt_items: 50          # 注入上下文的 Skill 摘要数量上限
  max_description_chars: 240    # 单个 Skill description 截断长度

memory:
  enabled: true                 # 是否启用 Memory 系统
  backend: markdown             # Markdown 是唯一真源；SQLite 索引库可重建
  root_dir: ".agent_memory"     # Memory 根目录，相对 project_class/
  long_term_file: "MEMORY.md"   # 长期记忆文件名（必须是文件名，不允许路径穿越）
  daily_dir: "daily"            # 每日记忆目录
  context:
    max_inject_chars: 16384     # 注入 system prompt 的 MEMORY.md 最大字符数
    inject_when_empty: true     # 即使 MEMORY.md 为空也注入空 <memory> 块
  search:
    keyword_enabled: true       # 使用 SQLite FTS5 做关键词检索
    vector_enabled: true        # 使用 sqlite-vec 做语义检索；不可用时退化为 FTS5-only
    hybrid_enabled: true        # 合并关键词与向量结果
    max_results: 5
    max_snippet_chars: 500
    embedding:
      provider: ollama
      base_url: "http://localhost:11434"
      model: "embeddinggemma"
      timeout: 30
  promotion:
    enabled: true
    min_recall_count: 3          # 每日记忆被命中达到阈值后可晋升
  write:
    max_text_chars: 2048        # 单条记忆 text 字符上限
    admission:
      explicit_user_intent: true        # 允许 source=explicit_user_request
      model_inferred_stable_fact: true  # 允许 source=model_inferred_stable_fact
    sensitive_patterns:                 # 命中任一正则则拒写
      - "(?i)api[_-]?key"
      - "\\b[0-9]{11}\\b"               # 11 位数字（手机号）
      - "(?i)password"
      - "(?i)cookie"
```

### 3.2 环境变量优先级

环境变量可覆盖 config.yaml 中的对应值：

| 环境变量 | 对应配置 |
|---|---|
| `AGENT_API_KEY` | `llm.api_key` |
| `AGENT_BASE_URL` | `llm.base_url` |
| `AGENT_MODEL` | `llm.model` |
| `AGENT_WORK_DIR` | `agent.work_dir` |
| `AGENT_MEMORY_DIR` | `memory.root_dir` |
| `AGENT_MEMORY_DISABLED` | 设为 `1` 时强制 `memory.enabled=false` |

### 3.3 config.py 实现逻辑

```
加载顺序（后者覆盖前者）：
1. config.yaml 默认值
2. .env 文件（通过 python-dotenv）
3. 系统环境变量
```

---

## 4. 上下文管理（context.py）

当前 `session.messages` 是持久化历史，但不应该直接等同于每次发送给 LLM 的完整上下文。新增 `ContextManager`，每次调用 LLM 前临时构建 OpenAI messages。

### 4.1 设计目标

- Session 只保存事实历史：user、assistant、assistant tool_calls、tool result。
- 系统提示词、Skill 摘要、Memory 内容、运行时状态等属于可重建上下文，不重复写入 Session。
- 每次请求都由 `ContextManager` 统一组装，便于后续增加裁剪、摘要、token 预算和确定性排序。
- Tool schema 仍通过 OpenAI `tools` 参数传入，不把完整 JSON schema 塞进 prompt。
- Memory（MEMORY.md 全文）作为长期上下文进入 system prompt，与 Skill 摘要一样按确定性顺序拼接。

### 4.2 ContextManager 接口

```python
class ContextManager:
    def __init__(
        self,
        config: AgentConfig,
        skill_registry: SkillRegistry,
        memory_store: MemoryStore | None = None,
    ) -> None: ...

    def build_messages(
        self,
        session: Session,
        tool_ctx: ToolContext,
    ) -> list[dict]:
        """
        构建发给 LLM 的 messages。
        不修改 session.messages，也不向 MEMORY.md 写入任何内容。
        """
```

### 4.3 上下文内容

每次请求的 messages 由以下部分组成：

1. `system` 消息（拼接顺序固定，便于 prompt cache 命中）：
   - `config.agent.system_prompt`
   - 当前工作目录、Session ID 等运行时状态
   - Skill 使用规则：当任务匹配 Skill 描述时，先调用 `skill_read` 读取 `SKILL.md`
   - Memory 使用规则：`<memory>` 是长期记忆，模型在跨会话稳定事实/偏好上应优先参考；显式写入需走 `memory_write` 工具。若用户明确要求“记住”，使用 `source=explicit_user_request`，直接进入长期记忆；其他观察先写入每日记忆，后续再按晋升机制沉淀
   - `<available_skills>` 摘要列表
   - `<memory>` 块（MEMORY.md 全文，超长时按 §7.7 规则裁剪）
2. `session.messages`：
   - 用户消息
   - assistant 最终回答
   - assistant tool_calls
   - role=tool 的工具结果（含 `memory_read`/`memory_search`/`memory_write` 的返回）

### 4.4 Skill 摘要注入格式

上下文中只暴露 Skill 名称和描述，不暴露完整 `SKILL.md` 内容：

```xml
<available_skills>
  <skill>
    <name>research</name>
    <description>Research a topic using local files and web-like tools, then summarize findings.</description>
  </skill>
  <skill>
    <name>calculator</name>
    <description>Perform calculation-heavy tasks with explicit verification steps.</description>
  </skill>
</available_skills>
```

若没有可用 Skill：

```xml
<available_skills />
```

### 4.5 Skill 使用规则

系统提示词应包含明确规则：

- `<available_skills>` 只是索引，不是完整说明。
- 当用户任务与某个 Skill 的 description 匹配时，先调用 `skill_read`。
- `skill_read` 返回 `SKILL.md` 内容后，再遵循其中的步骤、约束和相对路径引用。
- 如果 `SKILL.md` 提到 `references/`、`scripts/` 等相对路径，后续工具调用必须基于该 Skill 的目录解析。

### 4.6 Prompt Cache 稳定性

ContextManager 组装上下文时必须保持确定性：

- Skill 列表按名称排序。
- Tool 列表按启用顺序或名称稳定排序。
- Memory 块由 `MemoryStore.read_long_term_text()` 直接返回 MEMORY.md 全文；只要长期记忆未变化，注入内容字节级稳定。
- 不在 system prompt 中注入变化频繁的时间戳。
- 不重写历史消息内容。
- 如需裁剪历史，优先裁剪较新的尾部工具噪声或做显式摘要，避免无意破坏稳定前缀。

---

## 5. LLM 客户端（llm.py）

封装对 OpenAI-compatible API 的调用，与具体 provider 解耦。

### 5.1 接口设计

```python
class LLMClient:
    def __init__(self, config: LLMConfig): ...

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = False,
    ) -> str | Generator[str, None, None]:
        """
        发送对话请求。
        - messages: OpenAI 格式的消息列表
        - tools: 工具 schema 列表（OpenAI function calling 格式）
        - stream: 是否流式返回
        返回：完整回复字符串 或 流式 Generator
        """
```

### 5.2 消息格式

使用 OpenAI 标准格式：

```json
[
  {"role": "system", "content": "你是一个智能助手..."},
  {"role": "user", "content": "用户的问题"},
  {"role": "assistant", "content": "...", "tool_calls": [...]},
  {"role": "tool", "tool_call_id": "xxx", "content": "工具结果"}
]
```

### 5.3 工具调用格式（OpenAI Function Calling）

LLM 返回结构：
```json
{
  "role": "assistant",
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "read_file",
        "arguments": "{\"path\": \"README.md\"}"
      }
    }
  ]
}
```

> **设计决策**：使用 OpenAI Function Calling 格式替代原有的 ````json` 文本解析方式，更可靠，避免 JSON 提取的脆弱性。DeepSeek API 完全兼容此格式。

---

## 6. Skill 系统（skills.py）

Skill 采用类似 OpenClaw 的 AgentSkills 风格：每个 Skill 是一个目录，必须包含 `SKILL.md`。上下文中只注入 Skill 的 name/description 摘要；当模型判断需要使用某个 Skill 时，通过 `skill_read` 工具读取详细说明。

### 6.1 Skill 目录格式

```text
skills/
└── research/
    ├── SKILL.md
    ├── references/
    │   └── search-strategy.md
    └── scripts/
        └── helper.py
```

`SKILL.md` 必须包含 YAML frontmatter：

```markdown
---
name: research
description: Research a topic using available tools, verify evidence, and summarize findings.
---

# Research Skill

Use this skill when the user asks for research, comparison, or evidence-backed summaries.

Steps:
1. Clarify the target question.
2. Gather sources.
3. Summarize with citations or file references.
```

### 6.2 数据结构

```python
@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    file_path: Path      # SKILL.md 绝对路径
    base_dir: Path       # Skill 目录
    source: str          # workspace | user | bundled
```

```python
class SkillRegistry:
    def register(self, skill: Skill) -> None: ...
    def get(self, name: str) -> Skill | None: ...
    def list_for_prompt(self) -> list[Skill]: ...
```

### 6.3 加载规则

- 默认加载 `project_class/skills/*/SKILL.md`。
- 后续可扩展用户目录和内置目录，例如 `~/.agent/skills`、bundled skills。
- 同名 Skill 后加载者覆盖先加载者，便于工作区覆盖全局 Skill。
- 忽略隐藏目录和 `node_modules`。
- `SKILL.md` 必须包含 `name` 和 `description`。
- `description` 进入 prompt 前按 `max_description_chars` 截断。
- 读取文件时必须校验 resolved path 不逃逸对应 skills root。

### 6.4 Skill prompt 摘要

```python
def format_skills_for_prompt(skills: list[Skill]) -> str:
    ...
```

输出只包含 name/description，不包含完整 `SKILL.md`：

```xml
<available_skills>
  <skill>
    <name>research</name>
    <description>Research a topic using available tools, verify evidence, and summarize findings.</description>
  </skill>
</available_skills>
```

### 6.5 `skill_read` 工具

`skill_read` 是 Skill 系统的专用入口。模型通过名称选择 Skill，工具负责安全读取对应 `SKILL.md`。

| 工具名 | 描述 | 参数 |
|---|---|---|
| `skill_read` | 按 Skill 名称读取对应 `SKILL.md` 的完整说明 | `name: str` |

Schema：

```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "description": "要读取的 Skill 名称，必须来自 <available_skills>。"
    }
  },
  "required": ["name"]
}
```

返回内容应包含：

- Skill 名称
- Skill 基础目录
- `SKILL.md` 正文内容
- 相对路径解析提示

示例：

```text
[Skill] research
[BaseDir] /abs/path/project_class/skills/research

--- SKILL.md ---
...

相对路径说明：该 Skill 中提到的 references/ 或 scripts/ 路径都相对于 BaseDir 解析。
```

### 6.6 `skill_read` 与普通 `read_file` 的边界

- `skill_read` 只接受 Skill 名称，不接受任意路径。
- `skill_read` 只能读取已注册 Skill 的 `SKILL.md`。
- 普通 `read_file` 仍用于读取工作目录内普通文件或 Skill 详细说明中引用的资源。
- 如果后续允许读取 Skill 相对资源，可以新增 `skill_read_resource(name, path)`，不把这个能力混入第一版。

---

## 7. Memory 系统（memory.py）

Memory 系统用于在不污染 Session 历史的前提下，保留跨会话仍有价值的信息。设计参考 OpenClaw 的“长期记忆 + 每日记忆 + 检索索引”分层，但保留本项目一贯的简洁边界：Markdown 文件始终是真源，SQLite 中的 FTS5 与 sqlite-vec 只是可重建的派生索引。

### 7.1 设计目标

- 两层记忆：长期记忆保存稳定事实、偏好和项目约定；每日记忆保存当天观察、临时线索和待沉淀内容
- 三种长期作用域：`user`、`project`、`session`
- 显式准入：只有用户明确要求记住的内容，或由每日记忆晋升而来的内容，才能直接进入长期记忆；模型自行观察到的内容默认先进入每日记忆
- 可审阅：长期与每日记忆都用 Markdown 保存，人工可读、可手工编辑、可 git diff
- 可检索：使用 jieba 统一处理中文词项后写入 FTS5，使用 Ollama embedding + sqlite-vec 处理语义相似，`memory_search` 返回混合结果
- 可恢复：索引损坏或手工改文件后，可从 Markdown 真源执行完整重建
- 上下文克制：仅长期记忆全文注入 system prompt；每日记忆只在搜索命中时按需返回
- 与 Session 严格解耦：Session 是单会话事实历史，Memory 是跨会话知识层，两条持久化面彼此独立

### 7.2 目录与文件布局

默认路径相对 `project_class/`：

```text
project_class/
└── .agent_memory/
    ├── MEMORY.md              # 长期记忆，按 scope 分节
    ├── daily/
    │   └── YYYY-MM-DD.md      # 每日记忆，按日期追加
    └── memory.sqlite3         # SQLite 索引库：FTS5 关键词索引 + sqlite-vec 向量索引
```

- `MEMORY.md` 与 `daily/*.md` 是唯一真源。
- `memory.sqlite3` 是派生缓存，可删除后通过重建索引恢复。
- Session 对话历史不写入 `.agent_memory/`，仍由 `.sessions/<id>.json`（见 §10）持久化。

### 7.3 记忆分层

| 层级 | 文件 | 内容 | 读取方式 |
|---|---|---|---|
| 长期记忆 | `MEMORY.md` | 稳定事实、用户偏好、项目约定 | 每轮全文注入 `<memory>`；也可按需搜索 |
| 每日记忆 | `daily/YYYY-MM-DD.md` | 当天观察、任务线索、尚未确认是否长期有用的内容 | 不直接注入；通过 `memory_search` 按需召回 |

每日记忆的存在，是为了给系统一个“先记下来，再决定是否值得长期保留”的缓冲层，避免把所有一日噪声直接塞进 `MEMORY.md`。

### 7.4 文件格式

#### 7.4.1 `MEMORY.md`

```markdown
# Agent Memory

## user

- id: 7f5e1a3b-...
  created_at: 2026-04-18T10:00:00+08:00
  source: explicit_user_request
  reason: 用户明确说“记住我喜欢用 Python”
  text: 用户偏好 Python 3.10+。

## project

- id: 83c12d4f-...
  created_at: 2026-04-18T10:05:00+08:00
  source: promoted_daily_memory
  reason: 每日记忆多次被命中，说明具有长期复用价值
  promoted_from: daily/2026-04-18.md#2f8c...
  text: 本项目优先保持 Python 3.10+ 兼容，避免 3.12 独有语法。

## session
```

#### 7.4.2 `daily/YYYY-MM-DD.md`

```markdown
# Daily Memory 2026-04-18

- id: 2f8c...
  created_at: 2026-04-18T10:02:00+08:00
  source: model_observation
  text: 用户今天反复要求优先保持 Python 3.10+ 兼容。
  recall_count: 0
  promoted_to:
```

格式约定：
- 长期记忆固定三个二级标题：`## user`、`## project`、`## session`
- 每条长期记忆固定字段：`id` / `created_at` / `source` / `reason` / `text`，晋升项额外带 `promoted_from`
- 每条每日记忆固定字段：`id` / `created_at` / `source` / `text` / `recall_count` / `promoted_to`
- `text` 与 `reason` 写入时压成单行，便于稳定解析
- `daily/YYYY-MM-DD.md` 不要求按 scope 分节，保持轻量追加即可

### 7.5 数据结构

```python
MemoryScope = Literal["user", "project", "session"]
MemoryTier = Literal["long_term", "daily"]
MemorySource = Literal[
    "explicit_user_request",
    "model_inferred_stable_fact",
    "model_observation",
    "promoted_daily_memory",
]

@dataclass(frozen=True)
class MemoryItem:
    id: str
    tier: MemoryTier
    text: str
    created_at: str
    source: MemorySource
    scope: MemoryScope | None = None
    reason: str | None = None
    daily_date: str | None = None
    recall_count: int = 0
    promoted_from: str | None = None
    promoted_to: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class MemorySearchResult:
    item: MemoryItem
    score: float                  # 归一化分数 [0, 1]
    snippet: str                  # ≤ memory.search.max_snippet_chars
    matched_by: Literal["fts5", "vector", "hybrid"]
```

### 7.6 MemoryStore 接口

```python
class MemoryStore(Protocol):
    def write_with_policy(
        self,
        text: str,
        source: MemorySource,
        scope: MemoryScope | None = None,
        reason: str | None = None,
    ) -> str: ...

    def add_long_term(
        self,
        scope: MemoryScope,
        text: str,
        source: MemorySource,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> str: ...

    def add_daily(
        self,
        text: str,
        source: MemorySource,
        metadata: dict[str, Any] | None = None,
    ) -> str: ...

    def search(
        self,
        query: str,
        limit: int = 5,
        tier: MemoryTier | None = None,
        scope: MemoryScope | None = None,
    ) -> list[MemorySearchResult]: ...

    def promote_daily(self, item_id: str, scope: MemoryScope, reason: str) -> str: ...

    def rebuild_index(self) -> None:
        """重新扫描 MEMORY.md + daily/*.md，重建 FTS5 与 sqlite-vec。"""

    def read_long_term_text(self) -> str:
        """返回 MEMORY.md 全文，用于注入 system prompt。"""

    def list_all(
        self,
        tier: MemoryTier | None = None,
        scope: MemoryScope | None = None,
    ) -> list[MemoryItem]: ...
```

第一阶段实现仍可集中在 `memory.py` 中，不拆分成多模块：
- `MarkdownMemoryStore` 负责读写 Markdown 真源
- `MemoryIndexer` 负责维护 SQLite 内的 FTS5 与 sqlite-vec
- `KeywordTokenizer` 使用 jieba 的搜索模式处理中文分词，并补充英文单词、ID、错误码等正则词项
- `EmbeddingProvider` 第一阶段实现为 Ollama，通过 `/api/embed` 生成真实向量；索引与查询必须使用同一个 embedding model
- `MemoryStore` 对外组合两者，向调用方暴露单一入口

### 7.7 写入与索引一致性

`memory_write` 不再由调用方显式指定 `tier`，而是由写入策略自动分流：
- `source=explicit_user_request`：表示用户明确要求“记住”，必须同时提供 `scope` 与 `reason`，直接写入长期记忆
- `source=model_observation` 或 `source=model_inferred_stable_fact`：表示模型在工作中得到的观察或推断，统一先写入每日记忆
- `source=promoted_daily_memory`：仅由晋升流程内部使用，写入长期记忆
- 两类写入都要经过敏感内容拦截与长度限制

一致性规则：
1. Markdown 永远是真源。
2. 通过 `memory_write` 成功写入后，必须同步更新 SQLite 中的 FTS5 与 sqlite-vec 对应条目；FTS5 写入的是 jieba 分词后的词项串，Markdown 真源仍保留原文。
3. 若用户手工编辑了 Markdown，第一阶段不引入后台文件监听；通过 `memory_reindex` 或 CLI `/memory reindex` 显式重建索引。
4. 若 sqlite-vec 或 Ollama embedding 不可用，搜索自动退化为 FTS5-only；Markdown 写入不受影响。
5. 若 embedding provider/model 或向量维度改变，必须执行 `memory_reindex`，避免不同向量空间混写。
6. 后续若确实需要自动同步，可参考 OpenClaw 增加文件监听 + debounce + 增量 sync，但不作为本阶段默认复杂度。

### 7.8 搜索

`memory_search` 同时覆盖长期记忆与每日记忆：

```text
query
 ├─ FTS5 keyword search
 ├─ sqlite-vec vector search
 └─ merge + dedupe + top-k
```

- FTS5 擅长 ID、文件名、错误码、专有名词等精确匹配；中文文本在索引和查询两侧都统一使用 jieba 搜索模式分词，兼顾“科幻小说”命中“科幻”这类部分词查询
- sqlite-vec 擅长“说法不同但意思相近”的召回；向量由 Ollama embedding 模型生成
- `hybrid_enabled=true` 时按简单加权合并；若只有一路可用，则直接返回该路结果
- 第一阶段不引入 MMR、时间衰减、rerank 等高级策略，避免把检索系统做得比主体 Agent 更复杂
- 每次搜索若命中每日记忆，则将对应 `recall_count += 1`，为后续晋升提供信号

### 7.9 每日记忆晋升

晋升机制用于把“短期内被反复证明有用”的每日记忆沉淀为长期记忆。

第一阶段采用朴素规则：
- 用户明确要求长期记住：可直接写入长期记忆
- 每日记忆 `recall_count >= memory.promotion.min_recall_count`：进入可晋升候选
- 由 `promote_daily()` 将内容复制到 `MEMORY.md`，并写入 `promoted_from`
- 原每日记忆写回 `promoted_to`，避免重复晋升
- 该晋升链路用于承接 `model_observation` 与 `model_inferred_stable_fact` 这类非显式用户请求；用户明确要求“记住”的内容不经过每日层，直接落入长期记忆

不在第一阶段引入 OpenClaw 那种加权打分、查询多样性、时间衰减、dreaming 定时任务；先保留“够用且可解释”的闭环。

### 7.10 上下文注入

`ContextManager.build_messages` 在 system prompt 末尾附加 `<memory>` 块，内容仅为 `MEMORY.md` 全文：

```xml
<memory>
# Agent Memory
...
</memory>
```

约束：
- 注入顺序固定，保证 prompt cache 稳定
- 每日记忆不直接注入，避免日积月累撑爆上下文
- 当 `MEMORY.md` 为空时，若 `inject_when_empty=true`，仍注入空块说明
- 若长期记忆超长，仍按既有策略裁剪 `session`，优先保留 `user` 与 `project`

### 7.11 Memory 工具与 CLI

对模型暴露四个工具：

| 工具名 | 描述 | 主要参数 |
|---|---|---|
| `memory_read` | 读取长期记忆全文，或读取指定每日记忆文件 | `tier?: long_term\\|daily`, `date?: YYYY-MM-DD` |
| `memory_search` | 对长期/每日记忆做混合检索 | `query`, `limit?`, `tier?`, `scope?` |
| `memory_write` | 按写入策略保存记忆：用户明确要求记住时写长期记忆，其余先写每日记忆 | `text`, `source`, `scope?`, `reason?` |
| `memory_reindex` | 从 Markdown 真源重建 FTS5 与 sqlite-vec | 无 |

CLI 提供对应命令：
- `/memory show`
- `/memory daily [YYYY-MM-DD]`
- `/memory search <query>`
- `/memory status`
- `/memory reindex`
- `/memory promote <daily-item-id> <scope>`

`promote` 先只放在 CLI，不作为默认模型工具暴露，避免模型过早把每日噪声大量抬升为长期记忆。

### 7.12 与 Session 的关系

| 维度 | Session（`.sessions/<id>.json`） | Memory（`.agent_memory/`） |
|---|---|---|
| 生命周期 | 单个会话 | 跨会话保留 |
| 写入主体 | AgentLoop 自动追加消息 | 仅由 Memory 能力显式写入 |
| 内容粒度 | 完整对话事实历史 | 长期沉淀 + 每日观察 |
| 读取方式 | 加载 Session 时整体载入 | 长期记忆全文注入；每日记忆按需检索 |
| 清理方式 | `/clear` 创建新 Session | 人工编辑 Markdown，必要时执行 reindex |

### 7.13 实现边界

第一阶段明确不做：
- 文件监听与后台自动同步
- 多索引 backend
- MMR / temporal decay / rerank
- 自动 dreaming / cron 晋升
- session transcript 索引

这些能力都可以以后加，但此阶段的目标是先把“分层存储、混合检索、晋升、可重建”四根主梁搭稳。
---

## 8. 工具系统（tools.py）

### 8.1 工具注册机制

使用装饰器注册，工具元信息（name、description、schema）内聚在工具函数本身：

```python
TOOL_REGISTRY: dict[str, Tool] = {}

@tool(
    name="read_file",
    description="读取文件内容",
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（相对于工作目录）"}
        },
        "required": ["path"]
    }
)
def read_file(path: str, context: ToolContext) -> str:
    ...
```

`ToolContext` 包含当前工作目录等运行时状态，工具通过它读取/修改状态。

### 8.2 内置工具清单

| 工具名 | 描述 | 参数 |
|---|---|---|
| `skill_read` | 按 Skill 名称读取对应 `SKILL.md` | `name: str` |
| `memory_read` | 读取长期记忆全文，或读取指定每日记忆文件 | `tier?: long_term\|daily`, `date?: YYYY-MM-DD` |
| `memory_search` | 对长期/每日记忆做 FTS5 + sqlite-vec 混合检索 | `query: str`, `limit?: int`, `tier?: long_term\|daily`, `scope?: user\|project\|session` |
| `memory_write` | 按写入策略保存记忆：`explicit_user_request` 直接进长期记忆，其余先进入每日记忆 | `text`, `source`, `scope?`, `reason?` |
| `memory_reindex` | 从 Markdown 真源重建 FTS5 与 sqlite-vec | 无 |
| `read_file` | 读取文件内容 | `path: str`, `encoding?: str` |
| `write_file` | 写入文件内容 | `path: str`, `content: str`, `append?: bool` |
| `list_dir` | 列出目录内容 | `path?: str`（默认当前目录）|
| `change_dir` | 切换工作目录 | `path: str` |
| `shell` | 执行 shell 命令 | `command: str`, `timeout?: int` |
| `search_files` | 在目录中搜索文件 | `pattern: str`, `path?: str` |

> Memory 四个工具的注册由 `agent/memory.py` 的 `register_memory_tools(registry, memory_store, ...)` 完成；当 `memory_store is None`（即 Memory 被禁用）时不注册。

### 8.3 工具执行流程

```
loop.py 收到 tool_calls
  → 查找 TOOL_REGISTRY[tool_name]
  → 解析 arguments JSON
  → 执行工具函数，传入 ToolContext
  → 捕获异常，返回错误字符串（不中断 loop）
  → 构建 role=tool 消息追加到 messages
```

### 8.4 ToolContext

```python
@dataclass
class ToolContext:
    work_dir: str                            # 当前工作目录（可被 change_dir 修改）
    session_id: str                          # 当前 Session ID
    config: AgentConfig                      # 全局配置
    skill_registry: SkillRegistry | None = None
    memory_store: MemoryStore | None = None  # Memory 持久化访问（None 即禁用）
```

---

## 9. Agent Loop（loop.py）

### 9.1 ReAct 循环逻辑

```
输入：用户消息
  ↓
追加到 session.messages（role=user）
  ↓
┌─────────────────────────────────┐
│  while iteration < max_iter:    │
│    ContextManager.build_messages│
│    LLM.chat(messages, tools)    │
│    ↓                            │
│    有 tool_calls?                │
│    ├─ Yes → 执行工具             │
│    │         追加 assistant/tool │
│    │         结果到 session      │
│    │         continue           │
│    └─ No  → 这是最终回答        │
│              追加 assistant msg │
│              到 session         │
│              break              │
└─────────────────────────────────┘
  ↓
返回最终回答给 CLI 显示
```

### 9.2 loop.py 接口

```python
class AgentLoop:
    def __init__(
        self,
        config: AgentConfig,
        session: Session,
        llm: LLMClient,
        skill_registry: SkillRegistry,
        context_manager: ContextManager,
    ): ...

    def run(self, user_input: str) -> str:
        """
        执行一轮 Agent Loop。
        - 接收用户输入
        - 修改 session.messages（in-place）
        - 返回最终回答文本
        """

    def stream_run(self, user_input: str) -> Generator[str, None, None]:
        """流式版本，逐 token yield 最终回答"""
```

### 9.3 Messages 管理

- 每次 `run()` 将用户输入追加到 `session.messages`
- 工具调用过程中的中间消息（assistant tool_calls + tool results）也追加进去
- 最终回答的 assistant 消息追加进去
- `session.messages` 是持久化事实历史，不包含每轮临时构造的 Skill 摘要
- 发给 LLM 的 messages 由 `ContextManager` 基于 `session.messages` 重新构建
- `skill_read` 的返回结果作为 role=tool 消息写入 `session.messages`，因此模型后续轮次能看到已读取的 Skill 详细说明
- `memory_read`/`memory_search`/`memory_write` 的工具结果同样作为 role=tool 消息写入 `session.messages`；`memory_write` 成功后下一轮的 ContextManager 会自动注入更新后的 `<memory>`，模型无需在同轮再次读取

### 9.4 Skill 选择流程

```
用户：帮我做一次研究总结
  ↓
ContextManager 注入 <available_skills>，其中包含 research 的 name/description
  ↓
LLM 判断 research 匹配任务
  ↓
LLM tool_call: skill_read({"name": "research"})
  ↓
工具返回 research/SKILL.md 内容
  ↓
LLM 根据 SKILL.md 继续调用 read_file/search_files/shell 等工具
  ↓
最终回答
```

### 9.5 Memory 写入流程

```
用户："以后都按 PEP 8 写代码"
  ↓
ContextManager 注入 <memory>（包含已有长期记忆）+ Memory 使用规则
  ↓
LLM 识别"以后都按 ..." 为显式记忆意图
  ↓
LLM tool_call: memory_write({
    "text": "代码风格遵循 PEP 8。",
    "scope": "user",
    "source": "explicit_user_request",
    "reason": "用户明确说'以后都按 PEP 8 写代码'"
  })
  ↓
memory_store 校验 source/reason/scope/sensitive_pattern → 通过
  ↓
追加到 .agent_memory/MEMORY.md 的 ## user 段尾
  ↓
工具返回 {"id": "<uuid>"}（写入 session.messages）
  ↓
LLM 给出最终回答："已记住，以后会按 PEP 8 风格生成代码。"
  ↓
下一轮起，<memory> 块自动包含新条目
```

被拒绝示例：

```
LLM tool_call: memory_write({
    "text": "API_KEY=sk-...",
    ...
  })
  ↓
sensitive_patterns 命中 (?i)api[_-]?key
  ↓
返回 {"blocked": true, "reason": "sensitive_pattern"}
  ↓
LLM 据此调整：避免再次写入敏感片段，改为提示用户"该信息属于敏感凭据，未写入记忆"
```

---

## 10. Session 管理（session.py）

### 10.1 Session 数据结构

每个 Session 对应 `.sessions/<session-id>.json`：

```json
{
  "id": "20240420-143022-abc123",
  "created_at": "2024-04-20T14:30:22Z",
  "updated_at": "2024-04-20T15:10:05Z",
  "title": "用户对话摘要（取首条消息前30字）",
  "work_dir": ".",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "tool_calls": [...]},
    {"role": "tool", "tool_call_id": "xxx", "content": "skill_read 结果或普通工具结果"},
    {"role": "assistant", "content": "..."}
  ]
}
```

Session 索引 `.sessions/index.json`：

```json
{
  "sessions": [
    {
      "id": "20240420-143022-abc123",
      "title": "...",
      "created_at": "...",
      "updated_at": "..."
    }
  ]
}
```

> 说明：Session 文件不再持久化 system prompt。system prompt、Skill 摘要和运行时状态由 `ContextManager` 每次动态生成。

### 10.2 Session ID 生成

```
格式：YYYYMMDD-HHMMSS-<随机6位十六进制>
示例：20240420-143022-a3f7b1
```

### 10.3 SessionManager 接口

```python
class SessionManager:
    def new_session(self) -> Session: ...
    def load_session(self, session_id: str) -> Session: ...
    def save_session(self, session: Session) -> None: ...
    def list_sessions(self) -> list[SessionMeta]: ...
    def delete_session(self, session_id: str) -> None: ...
    def get_latest_session(self) -> Session | None: ...
```

### 10.4 Session 生命周期

```
程序启动
  → 无 --session 参数：创建新 Session
  → 有 --session <id>：加载已有 Session（恢复历史）
  → 有 --resume：加载最近一次 Session

每轮对话结束后（auto_save=true）：
  → session.save()（更新文件 + 索引）

程序退出：
  → 最终 save
```

---

## 11. CLI 设计（cli.py）

### 11.1 启动方式

```bash
# 启动新会话
python main.py

# 恢复最近会话
python main.py --resume

# 恢复指定会话
python main.py --session 20240420-143022-a3f7b1

# 直接执行单条指令（非交互模式）
python main.py --message "列出当前目录的文件"

# 指定配置文件
python main.py --config /path/to/config.yaml
```

### 11.2 交互式 REPL

启动后进入交互循环，界面风格参考 openclaw：

```
Agent v0.1.0 | Session: 20240420-143022-a3f7b1 | 工作目录: project_class/
输入 /help 查看可用命令

> 你好，列出当前目录的内容

[调用工具] list_dir path="."
[工具结果] DESIGN.md  ReAct_agent_loop_tool_schema.py  agent/  ...

当前目录包含以下文件：
- DESIGN.md（设计文档）
- ReAct_agent_loop_tool_schema.py（原始参考代码）
...

> 
```

### 11.3 内置斜杠命令

| 命令 | 描述 |
|---|---|
| `/help` | 显示帮助信息，并列出 `/memory show|daily|search|status|reindex|promote` 的简要用法 |
| `/exit` 或 `/quit` | 退出程序 |
| `/session new` | 创建新 Session |
| `/session list` | 列出所有 Session |
| `/session load <id>` | 切换到指定 Session |
| `/session info` | 显示当前 Session 信息 |
| `/history` | 显示当前会话历史 |
| `/clear` | 清空当前会话历史（不删除文件，也不清空 Memory）|
| `/tools` | 显示所有可用工具 |
| `/skills` | 显示所有可用 Skill 的 name/description |
| `/skill <name>` | 显示指定 Skill 的摘要和 SKILL.md 路径（不注入对话） |
| `/memory` | 等价于 `/memory show` |
| `/memory show` | 打印 MEMORY.md 全文（人工审阅，不写入对话） |
| `/memory daily [YYYY-MM-DD]` | 打印指定日期的每日记忆；不传日期时显示今天 |
| `/memory search <query>` | 在本地终端搜索长期记忆与每日记忆，结果不进入模型上下文 |
| `/memory status` | 显示 FTS5、sqlite-vec、embedding provider/model、向量维度和最近错误 |
| `/memory reindex` | 从 Markdown 真源强制重建 FTS5 与 sqlite-vec |
| `/memory promote <daily-item-id> <scope>` | 将指定每日记忆晋升到长期记忆 |
| `/cd <path>` | 切换工作目录 |
| `/config` | 显示当前配置（含 memory 段） |

> Memory 显式增删走模型工具调用（`memory_write` 等）；CLI 不提供 `/memory write`，避免绕过准入策略产生未经 reason 的条目。

### 11.4 输出格式

- **工具调用**：灰色前缀 `[调用工具] tool_name args...`
- **工具结果**：灰色前缀 `[工具结果] ...`（超长时截断）
- **Skill 读取**：灰色前缀 `[读取 Skill] skill_name`
- **Memory 写入**：黄色前缀 `[记忆写入] scope=user source=explicit_user_request id=...`
- **Memory 拒绝**：黄色前缀 `[记忆拒写] reason=sensitive_pattern`
- **最终回答**：正常输出，支持流式打印
- **错误信息**：红色前缀 `[错误] ...`
- **系统提示**：蓝色前缀 `[系统] ...`

使用 `colorama` 库实现跨平台颜色支持。

### 11.5 readline 历史

使用 Python `readline` 模块，支持：
- 上下键浏览历史输入
- 历史保存到 `.sessions/.input_history`，跨会话持久化
- Tab 补全斜杠命令

---

## 12. 主入口（main.py）

```python
def main():
    args = parse_args()                 # argparse 解析命令行参数
    config = load_config(args)          # 加载 config.yaml + 环境变量
    skill_registry = load_skills(config)
    memory_store = load_memory(config)  # 禁用时返回 None
    register_memory_tools(memory_store) # memory_store is None 则跳过注册

    session_mgr = SessionManager(config)
    session = resolve_session(args, session_mgr)   # new / load / resume

    context_mgr = ContextManager(config, skill_registry, memory_store)
    loop = AgentLoop(
        config, session, llm,
        skill_registry, context_mgr,
        memory_store=memory_store,
    )
    cli = CLI(loop, session, session_mgr, config, memory_store=memory_store)
    cli.run()                           # 进入交互循环

if __name__ == "__main__":
    main()
```

`memory_store` 在四个组件间共享：

- `ContextManager`：每轮 `build_messages` 时调用 `memory_store.read_long_term_text()` 注入 `<memory>`。
- 工具注册：`register_memory_tools` 把 `memory_store` 注入 `memory_read`/`memory_search`/`memory_write`/`memory_reindex` 闭包。
- `AgentLoop`：把 `memory_store` 写入 `ToolContext`，工具执行时通过 ctx 拿到同一实例，避免重复加载。
- `CLI`：`/memory` 系列命令直接调用 `memory_store` 在终端显示，不经过模型上下文。

---

## 13. 依赖管理

### 13.1 依赖列表（requirements.txt）

```
openai>=1.0.0          # OpenAI SDK（兼容 DeepSeek）
python-dotenv>=1.0.0   # .env 文件支持
pyyaml>=6.0            # config.yaml 解析
colorama>=0.4.6        # 跨平台终端颜色
sqlite-vec             # SQLite 向量扩展（与 FTS5 同库）
jieba>=0.42.1          # 中文分词：统一供 FTS5 索引和查询使用
readline               # 标准库（Linux/Mac）；Windows 用 pyreadline3
```

### 13.2 Python 版本要求

Python >= 3.10（使用了 `match` 语句和 `X | Y` 类型注解）

---

## 14. 实现顺序（分阶段）

### Phase 1：核心可运行
1. `config.py` — 加载 config.yaml + 环境变量（含 memory 段，但 store 暂不启用）
2. `llm.py` — LLM 客户端，支持流式和非流式
3. `tools.py` — 工具注册机制 + 文件/目录/shell 等基础工具
4. `loop.py` — Agent Loop，基于 Function Calling
5. `main.py` — 基础非交互模式（`--message` 参数）

### Phase 2：Skill 与上下文管理
6. `skills.py` — Skill 发现、frontmatter 解析、注册表、prompt 摘要
7. `tools.py` — 增加 `skill_read` 工具
8. `context.py` — ContextManager 构建 system prompt + Skill 摘要 + session history
9. `loop.py` — 改为通过 ContextManager 构建 LLM messages

### Phase 3：Memory 系统
10. `memory.py` — `MemoryItem` / `MarkdownMemoryStore` / 长期记忆 + 每日记忆文件解析
11. `memory.py` — `MemoryIndexer`：FTS5、sqlite-vec、混合搜索、`rebuild_index`
12. `memory.py` — 每日记忆召回计数与 `promote_daily`
13. `memory.py` — `register_memory_tools`：注册 `memory_read` / `memory_search` / `memory_write` / `memory_reindex`
14. `context.py` — ContextManager 仅注入长期记忆 `<memory>` 块；裁剪策略
15. `tools.py` — `ToolContext` 增加 `memory_store` 字段
16. `main.py` — 调用 `load_memory(config)` 并贯穿 ContextManager / ToolContext / CLI

### Phase 4：CLI 交互
17. `session.py` — Session 持久化
18. `cli.py` — 交互式 REPL + 斜杠命令（含 `/memory show|daily|search|reindex|promote`）
19. `main.py` — 完整参数解析和会话恢复

### Phase 5：完善
20. 流式输出集成到 CLI
21. readline 历史和 Tab 补全
22. config.example.yaml、.gitignore（忽略派生索引；Markdown 真源是否提交由用户决定，文档说明）

---

## 15. 测试设计

### 15.1 测试策略

| 层次 | 覆盖范围 | 隔离方式 |
|---|---|---|
| 单元测试 | config / tools / skills / memory / context / session / llm 响应解析 | 不依赖网络，不依赖真实文件系统（用 tmp_path）|
| 集成测试 | loop + context + skills + memory + tools + session 联动 | Mock LLM，真实工具执行，临时目录 |
| 端到端测试（可选）| 完整 CLI 流程 | Mock LLM + 临时目录，验证输出 |

**原则**：
- 不调用真实 LLM（无需 API Key 即可运行测试）
- 用 `pytest` + `unittest.mock` 实现 LLM Mock
- 每个测试用例独立，使用 `tmp_path` fixture 隔离文件系统
- 测试文件放在 `tests/` 目录，与 `agent/` 平级

### 15.2 测试目录结构

```
project_class/
└── tests/
    ├── __init__.py
    ├── conftest.py              # 公共 fixture（tmp_path、mock config、mock LLM、mock memory）
    ├── test_config.py           # 配置加载测试
    ├── test_tools.py            # 工具执行测试
    ├── test_skills.py           # Skill 加载、注册、skill_read 测试
    ├── test_memory.py           # 分层记忆 + 索引 + memory_read/search/write/reindex 测试
    ├── test_context.py          # 上下文构建测试（含 <memory> 注入）
    ├── test_session.py          # Session 管理测试
    ├── test_llm.py              # LLM 响应解析测试（不访问网络）
    └── test_loop.py             # Agent Loop 集成测试（Mock LLM，含 memory_write 流程）
```

### 15.3 各模块测试用例

#### config.py 测试（test_config.py）

| 用例 | 说明 |
|---|---|
| `test_default_values` | 无 config.yaml 时，所有字段使用默认值 |
| `test_load_from_yaml` | config.yaml 中的值正确覆盖默认值 |
| `test_env_var_overrides_yaml` | 环境变量优先级高于 yaml |
| `test_env_var_overrides_default` | 无 yaml 时环境变量也能生效 |
| `test_missing_api_key_is_empty` | 未配置 api_key 时返回空字符串而非报错 |
| `test_resolved_work_dir_relative` | 相对路径的 work_dir 正确解析为绝对路径 |
| `test_resolved_work_dir_absolute` | 绝对路径的 work_dir 原样返回 |
| `test_resolved_session_dir` | session.dir 正确解析为绝对路径 |
| `test_formatted_system_prompt` | system_prompt 中 `{work_dir}` 占位符被正确替换 |
| `test_partial_yaml_uses_defaults` | yaml 只写部分字段，其余仍使用默认值 |
| `test_load_skills_config` | skills 配置正确加载 |
| `test_load_memory_config_defaults` | 未配置 memory 段时使用默认值（enabled=true、root_dir=.agent_memory）|
| `test_memory_disabled_via_env` | `AGENT_MEMORY_DISABLED=1` 强制关闭 memory |
| `test_long_term_file_must_be_filename` | `memory.long_term_file` 含 `/` 或 `..` 时启动报错 |
| `test_load_daily_memory_config_defaults` | 未配置 daily_dir / promotion 时使用默认值 |
| `test_memory_root_dir_resolved_relative_to_project_root` | 相对路径正确解析为绝对路径 |

#### tools.py 测试（test_tools.py）

| 用例 | 说明 |
|---|---|
| `test_read_file_success` | 正常读取已有文件，返回完整内容 |
| `test_read_file_not_found` | 文件不存在时返回含 `[错误]` 的字符串 |
| `test_read_file_with_line_range` | `start_line` / `max_lines` 参数生效 |
| `test_write_file_creates_file` | 写入新文件，文件确实被创建且内容正确 |
| `test_write_file_overwrite` | 覆盖模式下，旧内容被替换 |
| `test_write_file_append` | append=true 时内容追加而非覆盖 |
| `test_write_file_creates_parent_dir` | 目标目录不存在时自动创建 |
| `test_list_dir_returns_entries` | 列出目录条目，包含文件和子目录标记 |
| `test_list_dir_hidden_files` | show_hidden=false 时不显示 `.` 开头条目 |
| `test_list_dir_not_found` | 目录不存在时返回含 `[错误]` 的字符串 |
| `test_change_dir_success` | 切换到已有目录，ctx.work_dir 被更新 |
| `test_change_dir_not_found` | 目录不存在时返回错误，ctx.work_dir 不变 |
| `test_change_dir_affects_subsequent_tools` | change_dir 后，read_file 使用新目录解析路径 |
| `test_shell_success` | 执行合法命令，返回 stdout |
| `test_shell_stderr` | 命令有 stderr 输出时也被返回 |
| `test_shell_nonzero_exit` | 非零退出码时返回中包含退出码信息 |
| `test_shell_timeout` | 超时时返回含超时提示的错误字符串 |
| `test_search_files_by_pattern` | glob 模式匹配，返回匹配文件列表 |
| `test_search_files_with_keyword` | content_keyword 过滤，只返回含关键词的文件 |
| `test_search_files_not_found` | 无匹配时返回提示字符串 |
| `test_skill_read_success` | `skill_read` 按名称读取已注册 Skill 的 `SKILL.md` |
| `test_skill_read_unknown` | 读取不存在 Skill 时返回 `[错误]` |
| `test_skill_read_does_not_accept_path` | `skill_read` 只接受 name，不接受任意路径 |
| `test_memory_read_returns_file_text` | `memory_read(tier="long_term")` 返回 MEMORY.md 全文 |
| `test_memory_read_daily_by_date` | `memory_read(tier="daily", date=...)` 返回指定每日记忆 |
| `test_memory_read_disabled_returns_error` | `ToolContext.memory_store is None` 时返回 `[错误] Memory 系统未启用` |
| `test_memory_search_filters_by_scope` | `memory_search(scope="user")` 只返回长期 user 段命中 |
| `test_memory_search_hybrid_merges_fts_and_vector` | FTS5 与 sqlite-vec 命中会合并去重 |
| `test_memory_search_falls_back_to_fts_only` | sqlite-vec 不可用时仍可通过 FTS5 搜索 |
| `test_memory_write_routes_explicit_request_to_long_term` | `source=explicit_user_request` 时直接写入 MEMORY.md |
| `test_memory_write_routes_non_explicit_to_daily` | 非显式用户请求时创建 `daily/YYYY-MM-DD.md` 并追加记录 |
| `test_memory_write_blocks_sensitive` | text 命中 `(?i)api[_-]?key` 时返回 `{"blocked": true, "reason": "sensitive_pattern"}` |
| `test_memory_write_requires_reason` | reason 为空时返回 `{"blocked": true, "reason": "missing_reason"}` |
| `test_memory_write_rejects_invalid_scope` | scope 不在 enum 中时返回 `scope_invalid` |
| `test_memory_write_rejects_oversize_text` | text 超过 `max_text_chars` 时返回 `too_long` |
| `test_execute_tool_unknown` | 调用不存在的工具名返回 `[错误]` 而不抛异常 |
| `test_tool_exception_is_caught` | 工具内部抛出异常时，execute_tool 返回错误字符串 |

#### skills.py 测试（test_skills.py）

| 用例 | 说明 |
|---|---|
| `test_load_skill_from_directory` | 从 `skills/<name>/SKILL.md` 加载 name/description |
| `test_skill_name_defaults_to_directory` | frontmatter 无 name 时使用目录名 |
| `test_missing_description_is_skipped` | 缺少 description 的 Skill 不注册 |
| `test_invalid_frontmatter_is_skipped` | frontmatter 无效时跳过并记录诊断 |
| `test_duplicate_skill_last_wins` | 同名 Skill 后加载者覆盖先加载者 |
| `test_list_for_prompt_sorted` | prompt 列表按名称稳定排序 |
| `test_format_skills_for_prompt` | 输出 `<available_skills>` 且只含 name/description |
| `test_empty_skills_prompt` | 无 Skill 时输出 `<available_skills />` |
| `test_skill_file_size_limit` | 超过 `max_file_bytes` 的 `SKILL.md` 被跳过 |
| `test_skill_path_escape_rejected` | symlink 或 resolved path 逃逸 root 时被拒绝 |

#### memory.py 测试（test_memory.py）

| 用例 | 说明 |
|---|---|
| `test_ensure_file_creates_skeleton` | MEMORY.md 不存在时初始化包含 `# Agent Memory` 与三个 `## scope` 标题 |
| `test_ensure_daily_file_creates_today_file` | 首次写入 daily 时自动创建当天文件 |
| `test_add_long_term_appends_to_correct_scope` | scope=user 时记录追加到 `## user` 段尾，不影响其他 scope |
| `test_add_daily_returns_uuid` | `add_daily` 返回的 id 是合法 UUID 字符串 |
| `test_write_policy_routes_explicit_request_to_long_term` | 显式用户请求直接进入长期记忆 |
| `test_write_policy_routes_non_explicit_memory_to_daily` | 非显式记忆默认先进入每日记忆 |
| `test_write_compresses_multiline_text` | text 含换行/多空格时写入后被压成单行，往返解析无损 |
| `test_read_text_roundtrip` | 写入 N 条后 `read_text` 返回的内容能被 `_read_items` 解析回 N 条 MemoryItem |
| `test_fts5_exact_match` | FTS5 能命中错误码、ID、专有名词等精确词 |
| `test_fts5_chinese_search_uses_jieba_tokens` | 中文文本经 jieba 分词后可被中文查询命中 |
| `test_vector_semantic_match` | sqlite-vec 能召回措辞不同但语义相近的条目 |
| `test_search_filter_scope` | scope 过滤生效，跨 scope 的长期记忆不返回 |
| `test_search_zero_score_excluded` | 完全不命中的 item 不出现在结果中 |
| `test_search_snippet_truncated` | snippet 长度 ≤ `max_snippet_chars` |
| `test_long_term_rejects_non_explicit_source` | `model_observation` / `model_inferred_stable_fact` 不能绕过策略直接写入长期记忆 |
| `test_sensitive_pattern_blocks_write` | 命中默认敏感模式时不写文件 |
| `test_concurrent_writes_serialize` | 同进程顺序两次写入，两条都可被读到（read-modify-write 不丢失） |
| `test_invalid_scope_section_ignored` | MEMORY.md 中出现 `## random` 时解析时被忽略，不影响 user/project/session |
| `test_daily_recall_increments_count` | 搜索命中 daily 条目后 `recall_count += 1` |
| `test_promote_daily_copies_to_long_term` | 达到阈值的 daily 条目可晋升到 MEMORY.md |
| `test_promote_daily_marks_source_item` | 晋升后原 daily 条目写入 `promoted_to`，不会重复晋升 |
| `test_rebuild_index_recreates_fts_and_vector_tables` | 删除索引后可从 Markdown 真源完整重建 |
| `test_register_memory_tools_skipped_when_store_none` | `memory_store is None` 时四个工具未进入 TOOL_REGISTRY |

#### context.py 测试（test_context.py）

| 用例 | 说明 |
|---|---|
| `test_context_includes_system_prompt` | 构建 messages 时包含 system prompt |
| `test_context_includes_available_skills` | system prompt 中包含 Skill 摘要 |
| `test_context_does_not_persist_system_prompt` | 构建上下文不修改 session.messages |
| `test_context_includes_history` | user/assistant/tool 历史被带入 LLM messages |
| `test_context_mentions_skill_read_rule` | system prompt 明确要求匹配 Skill 后先调用 `skill_read` |
| `test_context_skill_order_is_deterministic` | Skill 摘要顺序稳定 |
| `test_context_escapes_skill_xml` | name/description 中 XML 特殊字符被转义 |
| `test_context_includes_memory_block` | system prompt 末尾包含 `<memory>...</memory>` 且内容等于 `MemoryStore.read_long_term_text()` |
| `test_context_memory_block_when_empty` | MEMORY.md 为空且 `inject_when_empty=true` 时仍注入 `<memory>` 块（含说明文案） |
| `test_context_memory_omitted_when_disabled` | `memory_store is None` 时不出现 `<memory>` 标签 |
| `test_context_memory_truncated_when_too_long` | 超过 `max_inject_chars` 时按 scope 顺序裁剪，保留 user/project，最后裁 session |
| `test_context_memory_truncation_does_not_modify_file` | 裁剪只影响注入内容，MEMORY.md 文件不变 |
| `test_context_memory_rule_present` | system prompt 包含 Memory 使用规则文案 |
| `test_context_order_stable_for_cache` | 多次调用 `build_messages` 产生字节级一致的 system 消息（前提：MEMORY.md/Skills 不变） |

#### session.py 测试（test_session.py）

| 用例 | 说明 |
|---|---|
| `test_new_session_has_unique_id` | 连续创建两个 Session，ID 不相同 |
| `test_new_session_id_format` | Session ID 符合 `YYYYMMDD-HHMMSS-xxxxxx` 格式 |
| `test_save_and_load_roundtrip` | 保存后加载，messages 和元信息完整还原 |
| `test_save_creates_index` | 首次保存后 index.json 被创建 |
| `test_save_updates_index` | 多次保存同一 Session，index 中只有一条记录 |
| `test_list_sessions_sorted` | list_sessions 按 updated_at 降序排列 |
| `test_load_nonexistent_raises` | 加载不存在的 ID 抛出 FileNotFoundError |
| `test_delete_session` | 删除后文件消失，index 中也无记录 |
| `test_delete_nonexistent_returns_false` | 删除不存在的 ID 返回 False，不报错 |
| `test_get_latest_session` | 返回最近更新的 Session |
| `test_get_latest_session_empty` | 无任何记录时返回 None |
| `test_auto_title_from_first_user_message` | 首条 user 消息自动成为 title |
| `test_touch_updates_updated_at` | touch() 后 updated_at 变为当前时间 |

#### llm.py 测试（test_llm.py）

> 本模块不访问真实网络，仅测试响应解析逻辑（Mock `OpenAI` 客户端）。

| 用例 | 说明 |
|---|---|
| `test_blocking_response_content` | 非流式响应正确提取 content 文本 |
| `test_blocking_response_tool_calls` | 非流式响应正确解析 tool_calls（含 arguments JSON）|
| `test_blocking_response_no_tool_calls` | 无 tool_calls 时，has_tool_calls 为 False |
| `test_blocking_to_message` | to_message() 返回正确的 assistant dict |
| `test_streaming_response_content` | 流式响应拼接后 content 正确 |
| `test_streaming_response_tool_calls` | 流式 tool_calls 各 chunk 正确合并 |
| `test_streaming_stream_text` | stream_text() 逐 chunk yield 文本 |

#### loop.py 测试（test_loop.py）

> 使用 Mock LLM，不调用真实 API。

| 用例 | 说明 |
|---|---|
| `test_direct_answer_no_tools` | LLM 直接回答（无 tool_calls），messages 中只有 user + assistant |
| `test_system_prompt_injected` | 首次 run() 后 messages[0] 是 system 消息 |
| `test_context_manager_used_for_llm_messages` | LLM 收到的是 ContextManager 构建的 messages |
| `test_single_tool_call` | LLM 返回一次工具调用，工具执行后再次调用 LLM 得到最终回答 |
| `test_skill_read_then_final_answer` | LLM 先调用 `skill_read`，再基于 Skill 内容给出最终回答 |
| `test_skill_read_result_is_persisted` | `skill_read` 的 tool result 写入 session.messages |
| `test_memory_write_then_visible_next_round` | 第一轮 memory_write 成功后，第二轮 ContextManager 注入的 `<memory>` 包含新条目 |
| `test_memory_write_blocked_does_not_modify_file` | sensitive 命中时 MEMORY.md 字节级不变，但 tool 返回写入 session |
| `test_memory_write_result_is_persisted` | memory_write 的 tool result 写入 session.messages，可被后续 turn 看到 |
| `test_multi_tool_calls_in_one_turn` | 单次响应包含多个 tool_calls，全部执行后继续 |
| `test_multiple_iterations` | 需要多轮工具调用才能得到最终回答 |
| `test_max_iterations_reached` | 超过 max_iterations 时返回警告字符串 |
| `test_messages_appended_correctly` | 验证 messages 的 role 序列：system/user/assistant/tool.../assistant |
| `test_tool_error_does_not_stop_loop` | 工具执行出错时，loop 继续而非崩溃 |
| `test_on_tool_call_callback_fired` | on_tool_call 回调在工具调用前被触发 |
| `test_on_tool_result_callback_fired` | on_tool_result 回调在工具执行后被触发 |
| `test_stream_run_yields_tokens` | stream_run() 正确 yield 最终回答的各 token |
| `test_context_work_dir_updated_by_change_dir` | change_dir 工具修改后，loop.tool_ctx.work_dir 随之更新 |

### 15.4 公共 Fixture（conftest.py）

```python
# tests/conftest.py

@pytest.fixture
def tmp_work_dir(tmp_path):
    """临时工作目录，隔离文件系统操作。"""
    return tmp_path

@pytest.fixture
def tool_ctx(tmp_work_dir, memory_store):
    """预配置好的 ToolContext，工作目录指向临时目录，并注入临时 memory_store。"""
    return ToolContext(
        work_dir=tmp_work_dir,
        session_id="test-session",
        memory_store=memory_store,
    )

@pytest.fixture
def skill_registry(tmp_path):
    """预配置好的 SkillRegistry，包含测试 Skill。"""
    ...

@pytest.fixture
def memory_store(tmp_path):
    """指向临时目录的 MarkdownMemoryStore（默认敏感模式 + 默认 admission）。"""
    return MarkdownMemoryStore(path=tmp_path / ".agent_memory" / "MEMORY.md")

@pytest.fixture
def memory_disabled_store():
    """返回 None，用于测试 memory 禁用路径。"""
    return None

@pytest.fixture
def default_config(tmp_work_dir, tmp_path):
    """返回测试用 AgentConfig（无需真实 config.yaml）。"""
    cfg = AgentConfig()
    cfg.llm.api_key = "test-key"
    cfg.work_dir = str(tmp_work_dir)
    cfg.project_root = tmp_work_dir
    cfg.session.dir = str(tmp_path / ".sessions")
    cfg.skills.dirs = [str(tmp_path / "skills")]
    cfg.memory.root_dir = str(tmp_path / ".agent_memory")
    return cfg

@pytest.fixture
def session_mgr(tmp_path):
    """指向临时目录的 SessionManager。"""
    return SessionManager(tmp_path / ".sessions")

@pytest.fixture
def mock_llm_direct_answer():
    """Mock LLMClient：直接返回文本，无工具调用。"""
    # 返回固定文本 "这是回答"
    ...

@pytest.fixture
def mock_llm_one_tool_call():
    """Mock LLMClient：第一次返回工具调用，第二次返回最终回答。"""
    ...
```

### 15.5 运行方式

```bash
# 安装测试依赖
pip install pytest pytest-cov

# 运行所有测试
cd project_class
pytest tests/ -v

# 运行单个模块测试
pytest tests/test_tools.py -v

# 生成覆盖率报告
pytest tests/ --cov=agent --cov-report=term-missing
```

### 15.6 测试依赖补充（requirements.txt）

```
pytest>=7.0
pytest-cov>=4.0
```

---

## 16. 关键设计决策说明

| 决策 | 理由 |
|---|---|
| 使用 OpenAI Function Calling 格式 | 比原始 ```json 文本解析更可靠，DeepSeek 完全支持，结构化 tool_call_id 便于追踪 |
| Session 用 JSON 文件存储 | 简单、可读、无需数据库依赖，参考 openclaw sessions 目录的设计思路 |
| ToolContext 传入工具函数 | 工具可访问当前工作目录、Skill 注册表、Memory store 等状态，change_dir 修改的目录对后续工具生效 |
| Skill 只在 prompt 中注入 name/description | 控制上下文体积，保持类似 OpenClaw 的按需读取模型 |
| 使用 `skill_read` 读取 `SKILL.md` | 避免把 Skill 文件路径当作模型必须理解的接口，并集中做路径安全检查 |
| ContextManager 临时构建上下文 | Session 只保存事实历史，system prompt、Skill 摘要、Memory 全文等可重建内容不污染持久化历史 |
| Memory 用 Markdown 做真源 | 文件人工可读、可手工编辑、可 git diff；FTS5 与 sqlite-vec 只做可重建的派生索引，不反客为主 |
| 长期记忆与每日记忆分层 | 长期记忆承载稳定事实，每日记忆承载当天观察；先给信息一个缓冲层，再决定是否沉淀，能避免把短期噪声直接写进 `MEMORY.md` |
| 长期记忆全文注入、每日记忆按需召回 | 长期记忆是模型的“恒定背景”，每日记忆更像工作台草稿；两者生命周期不同，注入策略也应不同 |
| 混合检索采用 jieba + FTS5 + Ollama embedding + sqlite-vec | jieba 让 FTS5 真正理解中文词边界，FTS5 擅长精确词，sqlite-vec 擅长语义近邻；两路同库完成，既覆盖中文实际需求，也比双存储更简单 |
| 提供 `memory_reindex` | 既然索引是派生缓存，就必须能从 Markdown 真源完全重建；这是索引系统的恢复路径，而不是附属功能 |
| Memory 写入强制 source + reason | 防止把临时工具结果污染长期记忆；reason 让人工审阅 MEMORY.md 时能判断保留依据，便于后期清理 |
| Memory 敏感正则前置拦截 | API key/手机号/password/cookie 等不应进入持久化文件；store 层一致拦截，避免每个调用方各自判断 |
| Memory 与 Session 不共用存储 | 两者生命周期、写入主体、清理粒度都不同；强行合并会让 `/clear` 语义模糊（清掉对话还是清掉记忆？），独立存储让"清对话不清记忆"成为默认行为 |
| Memory 工具不暴露 `memory_delete` | 第一阶段保持只追加，删除需要人工编辑 MEMORY.md，避免模型自行清理产生不可追溯的丢失；后续可在管控好审计后再加 |
| CLI `/memory` 允许 `reindex` 与 `promote` | `reindex` 是索引恢复通道，`promote` 是人工把关入口；两者都属于运维与整理，不等同于绕过写入准入 |
| config.yaml + 环境变量双轨 | yaml 适合复杂配置，环境变量便于 CI/部署场景覆盖，.env 保护敏感信息 |
| 使用 openai SDK 而非 langchain | 减少依赖链，直接控制 API 调用行为，便于调试；原始代码的 langchain 主要用于工具包装，可直接替代 |
| 工作目录锁定在 project_class/ | 明确边界，避免工具操作影响父目录；`change_dir` 只在此范围内生效（可配置是否允许越界）|
