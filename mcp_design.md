# MCP Client 简化设计

## 目标

在当前简化 agent 中支持 MCP client，让外部 MCP server 暴露的 tools 可以像本地 `read_file`、`shell`、`memory_search` 一样进入 LLM 的 function calling 流程。

当前项目已有的主链路是：

1. `config.yaml` 读取配置到 `AgentConfig`
2. `agent.tools.TOOL_REGISTRY` 保存本地工具
3. `AgentLoop` 用 `get_openai_schemas()` 把工具 schema 传给 LLM
4. LLM 返回 `tool_calls`
5. `execute_tool()` 同步执行工具，并把结果作为 `role=tool` 放回 session

MCP 应该接在第 2 到第 5 步之间：启动 MCP client，发现远端 tools，把它们包装成当前项目已有的 `ToolDef`。

## 参考 openclaw 的取舍

参考目录：`C:\hsq\code\github\openclaw`，跳过所有 `project_class*` 目录。

openclaw 的相关做法可以借鉴，但当前项目只做简化版：

- 借鉴：
  - 配置使用 `mcp.servers.<name>`。
  - 支持 stdio server：`command`、`args`、`env`、`cwd`。
  - tool 名称使用 `server__tool`，避免不同 server 的 tool 重名。
  - 启动后 `listTools()`，把 MCP tool 的 `inputSchema` 转成 OpenAI tool schema。
  - 调用时再路由回对应 MCP server 的 `callTool()`。

- 暂不做：
  - HTTP/SSE/streamable-http transport。
  - 插件 bundle 合并。
  - SecretRef、header 插值、复杂权限系统。
  - MCP resources/prompts。
  - 动态热重载。

第一版只支持 stdio MCP tools。

## 配置格式

在 `config.yaml` 增加：

```yaml
mcp:
  enabled: true
  servers:
    filesystem:
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
      cwd: "."
      env: {}
      timeout: 30
```

对应新增配置类：

- `MCPConfig`
  - `enabled: bool = False`
  - `servers: dict[str, MCPServerConfig]`

- `MCPServerConfig`
  - `command: str`
  - `args: list[str] = []`
  - `env: dict[str, str] = {}`
  - `cwd: str | None = None`
  - `timeout: int = 30`

## 新增模块

建议新增 `agent/mcp.py`。

核心对象：

```python
class MCPTool:
    server_name: str
    remote_name: str
    local_name: str
    description: str
    schema: dict

class MCPManager:
    def start(self) -> None
    def stop(self) -> None
    def list_tool_defs(self) -> list[ToolDef]
    def call_tool(self, local_name: str, arguments: dict) -> str
```

职责：

1. 根据 `config.mcp.servers` 启动 stdio MCP client。
2. 对每个 server 调用 `list_tools()`。
3. 把每个远端 tool 包成当前项目的 `ToolDef`。
4. 执行时根据 `local_name -> (server, remote_tool)` 映射调用远端 `call_tool()`。
5. 把 MCP 返回的 content 统一格式化成字符串。

## 工具命名

本地暴露给 LLM 的名称：

```text
<server_name>__<tool_name>
```

例如：

```text
filesystem__read_file
github__search_issues
```

简化规则：

- 非 `A-Za-z0-9_-` 字符替换成 `_`
- 最长 64 字符
- 冲突时追加 `_2`、`_3`

这样不需要改 LLM loop，也能避免和本地工具重名。

## 和现有 Tool 系统集成

推荐不要让 MCP tool 直接写入全局 `TOOL_REGISTRY`，而是在运行时组合工具列表，避免测试污染和动态状态混在全局注册表里。

最小改动：

1. `tools.py`
   - 增加一个轻量函数：

```python
def get_openai_schemas_from_defs(tools: list[ToolDef]) -> list[dict]:
    return [t.to_openai_schema() for t in tools]
```

2. `AgentLoop.__init__`
   - 如果 `config.mcp.enabled`，创建 `MCPManager` 并 `start()`。
   - 保存：

```python
self._local_tools = get_enabled_tools(config.enabled_tools)
self._mcp_tools = self._mcp_manager.list_tool_defs()
self._runtime_tools = self._local_tools + self._mcp_tools
```

3. `AgentLoop.run()` / `stream_run()`
   - tool schema 从 `_runtime_tools` 生成。
   - 执行时先查本地工具，再查 MCP 工具：

```python
if tool_name in TOOL_REGISTRY:
    result = execute_tool(tool_name, args, self._tool_ctx)
else:
    result = self._mcp_manager.call_tool(tool_name, args)
```

4. CLI `/tools`
   - 继续显示本地工具。
   - 额外显示 MCP tools，标记来源 server。

## 依赖

`requirements.txt` 增加：

```text
mcp>=1.0.0
```

Python MCP SDK 是 async API，而当前 agent loop 是同步结构。第一版建议在 `MCPManager` 内部封装 async，不扩散到 `AgentLoop`：

- `start()` 内部用 `asyncio.run()` 初始化。
- `call_tool()` 内部用同步 wrapper 调用 async。

如果后续 MCP 调用频率高，再把 `AgentLoop` 改成 async；第一版不需要。

## 生命周期

第一版采用 session 级生命周期：

1. CLI 创建 `AgentLoop`
2. `AgentLoop` 启动 MCP servers
3. 整个 session 复用这些连接
4. CLI 退出或切换 session 时关闭连接

需要在 `AgentLoop` 增加：

```python
def close(self) -> None:
    if self._mcp_manager:
        self._mcp_manager.stop()
```

CLI 在 `_reset_loop()` 前，如果已有 loop，先调用 `close()`。

## 错误处理

简化策略：

- server 启动失败：跳过该 server，CLI 或日志显示错误。
- `list_tools` 失败：跳过该 server。
- 单个 MCP tool 调用失败：返回 `[错误] MCP tool <name> 执行失败: <message>`，不打断 agent loop。
- MCP 返回非文本 content：转成 JSON 字符串。

这和当前 `execute_tool()` 的“工具错误变成文本结果”一致。

## 测试计划

新增 `tests/test_mcp.py`：

1. 配置解析：
   - `mcp.enabled`
   - `mcp.servers.<name>.command/args/env/cwd/timeout`

2. tool 名称转换：
   - `server + remote tool -> server__tool`
   - 特殊字符清理
   - 冲突追加后缀

3. MCP manager 单元测试：
   - 用 fake MCP client 返回 tools
   - 验证生成的 `ToolDef.to_openai_schema()`
   - 验证 `call_tool()` 路由到正确 server/tool

4. AgentLoop 集成测试：
   - mock LLM 返回 MCP tool call
   - 验证 tool result 被追加成 `role=tool`
   - 验证最终回答正常返回

## 推荐实现顺序

1. 扩展 `config.py` 和 `config.example.yaml`。
2. 新增 `agent/mcp.py`，先用 fake client 写通测试。
3. 改 `AgentLoop` 支持 runtime tools。
4. 接入真实 `mcp` Python SDK 的 stdio client。
5. 补 CLI `/tools` 展示。
6. 跑现有测试和新增 MCP 测试。

## 第一版验收标准

- `config.yaml` 能配置一个 stdio MCP server。
- 启动 agent 后能发现远端 MCP tools。
- LLM 能看到 MCP tool schema。
- LLM 调用 `server__tool` 时，agent 能调用远端 MCP server 并把结果放回对话。
- 本地 tools、skills、memory 现有行为不变。
