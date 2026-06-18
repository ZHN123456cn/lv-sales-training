# MCP Client Design

## Goal

Add remote MCP client support to the current agent while preserving the existing stdio MCP support.

The project already has a working MCP integration path:

1. `config.yaml` is loaded into `AgentConfig`.
2. `AgentLoop` starts `MCPManager` when `mcp.enabled` is true.
3. `MCPManager` connects to configured MCP servers and discovers tools with `list_tools()`.
4. Discovered MCP tools are converted into `ToolDef`.
5. The LLM receives local tools plus MCP tools through function calling schemas.
6. Tool calls are dispatched back through `MCPManager.call_tool()`.
7. Tool results are written back into the session as `role=tool` messages.

The new design keeps this flow and extends the transport layer from stdio-only to:

- `stdio`
- `sse`
- `streamable_http`

`sse` and `streamable_http` should both be implemented in the first remote-MCP phase.

## Non-Goals

- Do not support MCP resources or prompts in the first remote phase.
- Do not implement OAuth or dynamic client registration.
- Do not auto-read MCP resource URIs returned by tools.
- Do not retry MCP tool calls by default, because tools may have side effects.
- Do not expose TLS options in the first version. Use the SDK/http client default certificate validation.

## Configuration

### Server Shape

`mcp.servers.<name>` gains explicit transport configuration.

```yaml
mcp:
  enabled: true
  servers:
    filesystem:
      transport: stdio
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
      cwd: "."
      env: {}
      timeout: 30

    remote_docs:
      transport: sse
      url: "https://example.com/sse"
      headers:
        Authorization: "Bearer ${REMOTE_MCP_TOKEN}"
      connect_timeout: 10
      read_timeout: 300
      call_timeout: 60
      max_retries: 2
      retry_backoff_seconds: 0.5

    remote_search:
      transport: streamable_http
      url: "https://example.com/mcp"
      headers:
        Authorization: "Bearer ${REMOTE_SEARCH_TOKEN}"
      connect_timeout: 10
      read_timeout: 300
      call_timeout: 60
      max_retries: 2
      retry_backoff_seconds: 0.5
```

### Field Rules

Add fields to `MCPServerConfig`:

```python
transport: str = "stdio"
command: str = ""
args: list[str] = []
env: dict[str, str] = {}
cwd: str | None = None
url: str = ""
headers: dict[str, str] = {}
timeout: int = 30
connect_timeout: int | None = None
read_timeout: int | None = None
call_timeout: int | None = None
max_retries: int = 2
retry_backoff_seconds: float = 0.5
```

Validation rules:

- `transport: stdio` requires `command`.
- `transport: sse` requires `url`.
- `transport: streamable_http` requires `url`.
- Supported transports are exactly `stdio`, `sse`, and `streamable_http`.
- Old stdio configs remain supported: if `transport` is missing and `command` exists, treat it as `stdio`.
- For new remote configs, `url` is the complete MCP endpoint. Do not auto-append `/sse` or `/mcp`.
- Ignore unrelated fields for the chosen transport unless they create an obvious conflict.

Timeout rules:

- `timeout` remains the compatibility fallback.
- `connect_timeout` defaults to `timeout`.
- `read_timeout` defaults to `timeout` for stdio and a larger value for remote transports if needed.
- `call_timeout` defaults to `timeout`.
- Startup operations use `connect_timeout`: connect, initialize, and list tools.
- Tool calls use `call_timeout`.
- Long-running SSE/HTTP reads use `read_timeout`.

### Header Expansion

Remote transports support `headers`.

Header values may include environment interpolation:

```yaml
headers:
  Authorization: "Bearer ${REMOTE_MCP_TOKEN}"
  X-Workspace: "${WORKSPACE_ID}"
```

Rules:

- Support `${ENV_NAME}` anywhere inside the string.
- If an environment variable is missing, treat the server startup as failed and record the error in `mcp_server_errors`.
- Do not preserve unresolved placeholders.
- Do not print header values in CLI output.

## Transport Creation

Keep `agent/mcp.py` as the home of the first implementation.

Replace the current default client factory with a router:

```python
async def _create_mcp_client(
    server_name: str,
    server_config: MCPServerConfig,
    cwd: Path | None,
) -> Any:
    if server_config.transport == "stdio":
        return await _create_stdio_client(server_name, server_config, cwd)
    if server_config.transport == "sse":
        return await _create_sse_client(server_name, server_config, cwd)
    if server_config.transport == "streamable_http":
        return await _create_streamable_http_client(server_name, server_config, cwd)
    raise RuntimeError(f"Unsupported MCP transport: {server_config.transport}")
```

Keep separate creator functions:

- `_create_stdio_client`
- `_create_sse_client`
- `_create_streamable_http_client`

The existing `MCPManager` should continue to accept `client_factory` for tests.

### Stdio

The current stdio logic remains the baseline:

- Build `StdioServerParameters`.
- Merge `os.environ` and `server_config.env`.
- Resolve `cwd` through `AgentConfig.resolved_mcp_cwd()`.
- Enter `stdio_client(params)`.
- Enter `ClientSession(read_stream, write_stream)`.
- Store an internal exit stack on the session so `MCPManager.stop()` can close it.

### SSE

Use the official Python MCP SDK SSE client.

Expected shape:

```python
from mcp import ClientSession
from mcp.client.sse import sse_client

read_stream, write_stream = await stack.enter_async_context(
    sse_client(
        server_config.url,
        headers=resolved_headers,
        timeout=server_config.connect_timeout_or_timeout,
        sse_read_timeout=server_config.read_timeout_or_timeout,
    )
)
session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
```

The exact keyword names should be verified against the installed MCP SDK version during implementation.

### Streamable HTTP

Use the official Python MCP SDK Streamable HTTP client.

Expected shape:

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

read_stream, write_stream, _ = await stack.enter_async_context(
    streamablehttp_client(
        server_config.url,
        headers=resolved_headers,
        timeout=server_config.connect_timeout_or_timeout,
        sse_read_timeout=server_config.read_timeout_or_timeout,
    )
)
session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
```

The SDK return tuple and keyword names should be verified during implementation, because the MCP Python SDK has changed these APIs over time.

## Startup Lifecycle

Use the existing lifecycle:

1. `AgentLoop.__init__` creates `MCPManager`.
2. `MCPManager.start()` creates a background asyncio loop.
3. `MCPManager._start_async()` connects all configured servers.
4. Each server is initialized.
5. Each server's tools are discovered.
6. Tools are registered as runtime `ToolDef`.
7. `AgentLoop.close()` stops the manager.
8. CLI session reset and process exit call `close()`.

This means all MCP tools are known before the first LLM request, which matches the current function-calling architecture.

## Retry Policy

Retries apply only to server startup:

- connect
- initialize
- list tools

Retries do not apply to `call_tool()` by default.

Config:

```yaml
max_retries: 2
retry_backoff_seconds: 0.5
```

Behavior:

- Attempt count is `1 + max_retries`.
- Use exponential backoff based on `retry_backoff_seconds`.
- Record the final failure in `MCPManager._errors[server_name]`.
- Continue starting other servers.

Reasoning: startup is usually idempotent, but tool calls may create or mutate remote state.

## Tool Naming

Keep the existing naming scheme:

```text
<server_name>__<remote_tool_name>
```

Examples:

```text
filesystem__read_file
github__search_issues
remote_docs__lookup
```

Rules remain unchanged:

- Replace characters outside `A-Za-z0-9_-` with `_`.
- Trim to 64 characters.
- Resolve collisions with `_2`, `_3`, and so on.
- Do not include transport in the tool name.

## Tool Result Formatting

Extend the current formatting conservatively.

Current behavior:

- Text content becomes plain text.
- Strings and bytes become text.
- Dicts and Pydantic-like objects become JSON.
- Error results are prefixed with `[error]`.

Remote extension:

- Text remains the preferred result.
- Non-text content such as image, audio, embedded resource, or resource link should be returned as a compact JSON summary.
- Do not download resource URIs.
- Do not inline binary content.

Example summary:

```json
{"type":"image","mimeType":"image/png","uri":"mcp://server/resource/123"}
```

This keeps the `role=tool` message as a string and prevents uncontrolled context growth.

## AgentLoop Integration

The existing integration point should remain:

```python
self._local_tools = get_enabled_tools(self._enabled_tools)
self._mcp_tools = self._mcp_manager.list_tool_defs()
self._runtime_tools = self._local_tools + self._mcp_tools
```

Tool dispatch remains:

```python
if name in TOOL_REGISTRY:
    return execute_tool(name, arguments, self._tool_ctx)
if self._mcp_manager is not None:
    return self._mcp_manager.call_tool(name, arguments)
return execute_tool(name, arguments, self._tool_ctx)
```

No `AgentLoop` async rewrite is required. Keep `MCPManager` responsible for bridging sync agent code to async MCP clients through its background event loop.

## CLI Integration

Extend `/mcp list`.

For each server, show:

- server name
- enabled/disabled state
- transport
- `command`, `args`, `cwd` for stdio
- `url` for `sse` and `streamable_http`
- discovered tools
- startup error, if any

Do not show:

- `headers`
- environment values
- bearer tokens

Example:

```text
MCP servers:
  remote_docs [enabled]
    transport: sse
    url: https://example.com/sse
    tools:
      - remote_docs__lookup -> lookup: Search remote docs.

  remote_search [enabled]
    transport: streamable_http
    url: https://example.com/mcp
    error: Missing environment variable REMOTE_SEARCH_TOKEN for header Authorization
    tools: (none discovered)
```

## Error Handling

Startup:

- One bad server must not prevent other MCP servers from working.
- Store per-server errors in `MCPManager._errors`.
- `/mcp list` displays those errors.
- If a server fails after retries, no tools from that server are registered.

Tool call:

- Unknown local MCP tool returns `[error] Unknown MCP tool: <name>`.
- Failed MCP tool call returns `[error] MCP tool <name> failed: <message>`.
- Do not retry by default.

Header expansion:

- Missing environment variable is a startup error for that server.
- The error message should name the missing variable and header key, but not include secret values.

## Dependency

Update `requirements.txt` from:

```text
mcp>=1.0.0
```

to a minimum MCP Python SDK version that is known to include Streamable HTTP client support.

Do not pin an exact version unless tests prove the SDK API is unstable enough to require it.

During implementation, verify the current import paths and call signatures against the installed SDK:

- `mcp.client.sse.sse_client`
- `mcp.client.streamable_http.streamablehttp_client`

## File-Level Implementation Plan

### `agent/config.py`

- Extend `MCPServerConfig`.
- Parse `transport`, `url`, `headers`, timeout fields, and retry fields.
- Preserve old stdio config compatibility.
- Add helper properties or functions for effective timeout values.
- Keep `resolved_mcp_cwd()` for stdio.

### `agent/mcp.py`

- Change default client factory from `_create_stdio_client` to `_create_mcp_client`.
- Add `_create_sse_client`.
- Add `_create_streamable_http_client`.
- Add header interpolation helper.
- Add startup retry wrapper around connect/initialize/list-tools.
- Keep `MCPManager` public API stable:
  - `start()`
  - `stop()`
  - `list_tool_defs()`
  - `call_tool()`
  - `tools`
  - `errors`
- Extend `_format_content_item()` to summarize non-text content.

### `agent/cli.py`

- Update `_print_mcp_servers()` to show transport-aware fields.
- Show remote `url`.
- Hide headers.
- Keep existing `/mcp list` command.

### `config.example.yaml`

- Update MCP examples to include:
  - stdio server
  - SSE remote server
  - Streamable HTTP remote server
- Use environment-variable placeholders for remote auth examples.

### `requirements.txt`

- Raise the `mcp` minimum version to a version with Streamable HTTP client support.

### `tests/test_mcp.py`

Add tests for:

- Old stdio config remains valid.
- Explicit `transport: stdio` config works.
- `transport: sse` parses `url`, `headers`, timeout fields, and retry fields.
- `transport: streamable_http` parses the same remote fields.
- Header interpolation supports `Bearer ${TOKEN}`.
- Missing header env var records a server startup error.
- Client factory receives the expected transport config.
- Startup retries happen for connect/list-tools failures.
- Tool calls are not automatically retried.
- Tool names remain `server__tool`.
- `/mcp list` shows transport and url but does not print headers.

Use fake client factories rather than real network servers for the first implementation tests.

## Acceptance Criteria

- Existing stdio MCP tests continue to pass.
- A stdio MCP server can still be configured with the old shape.
- An SSE MCP server can be configured with `transport: sse` and `url`.
- A Streamable HTTP MCP server can be configured with `transport: streamable_http` and `url`.
- Remote auth headers can reference environment variables.
- Missing environment variables produce clear per-server errors.
- Server startup retries are applied.
- MCP tool calls are never retried by default.
- `/mcp list` displays transport-aware diagnostics without leaking secrets.
- LLM tool schemas include remote MCP tools exactly like local stdio MCP tools.

## Implementation Order

1. Extend config dataclasses and parsing.
2. Add transport router and remote client creators.
3. Add header interpolation.
4. Add startup retry.
5. Extend CLI display.
6. Update example config and dependency minimum.
7. Add fake-client tests for config, manager, retry, and CLI output.
8. Run the existing test suite.

## Notes From Current Design

This design intentionally keeps the original stdio MCP architecture:

- `MCPManager` remains the only owner of MCP sessions.
- The agent loop remains synchronous from the caller's point of view.
- MCP tools are represented as normal `ToolDef` values.
- Tool names remain stable and server-scoped.
- MCP failures are isolated per server.

The main change is replacing a stdio-only client creator with a transport-aware creator while keeping the rest of the agent's tool pipeline unchanged.
