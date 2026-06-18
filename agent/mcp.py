from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .config import AgentConfig, MCPServerConfig
from .tools import ToolContext, ToolDef


@dataclass
class MCPTool:
    server_name: str
    remote_name: str
    local_name: str
    description: str
    schema: dict[str, Any]


ClientFactory = Callable[[str, MCPServerConfig, Optional[Path]], Any]


class MCPManager:
    def __init__(
        self,
        config: AgentConfig,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._config = config
        self._client_factory = client_factory or _create_mcp_client
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._clients: dict[str, Any] = {}
        self._tools: dict[str, MCPTool] = {}
        self._errors: dict[str, str] = {}

    def start(self) -> None:
        if not self._config.mcp.enabled or not self._config.mcp.servers:
            return
        if self._loop is not None:
            return

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="mcp-client-loop", daemon=True)
        self._thread.start()
        self._run_coroutine(self._start_async())

    def stop(self) -> None:
        if self._loop is None:
            return
        try:
            try:
                self._run_coroutine(self._stop_async())
            except Exception:
                pass
        finally:
            loop = self._loop
            loop.call_soon_threadsafe(loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._loop = None
            self._thread = None

    def list_tool_defs(self) -> list[ToolDef]:
        return [
            ToolDef(
                name=tool.local_name,
                description=tool.description,
                schema=tool.schema,
                func=_mcp_tool_placeholder,
            )
            for tool in self._tools.values()
        ]

    @property
    def tools(self) -> list[MCPTool]:
        return list(self._tools.values())

    @property
    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def call_tool(self, local_name: str, arguments: dict[str, Any]) -> str:
        if local_name not in self._tools:
            return f"[error] Unknown MCP tool: {local_name}"
        try:
            return self._run_coroutine(self._call_tool_async(local_name, arguments))
        except Exception as exc:
            return f"[error] MCP tool {local_name} failed: {exc}"

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_coroutine(self, coro: Any) -> Any:
        if self._loop is None:
            raise RuntimeError("MCP manager is not started")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    async def _start_async(self) -> None:
        self._exit_stack = AsyncExitStack()
        for server_name, server_config in self._config.mcp.servers.items():
            await self._start_server_with_retries(server_name, server_config)

    async def _start_server_with_retries(
        self,
        server_name: str,
        server_config: MCPServerConfig,
    ) -> None:
        attempts = max(0, server_config.max_retries) + 1
        delay = max(0.0, server_config.retry_backoff_seconds)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                await self._start_server_once(server_name, server_config)
                self._errors.pop(server_name, None)
                return
            except Exception as exc:
                last_error = exc
                if attempt < attempts - 1 and delay > 0:
                    await asyncio.sleep(delay * (2 ** attempt))
        if last_error is not None:
            self._errors[server_name] = str(last_error) or last_error.__class__.__name__

    async def _start_server_once(
        self,
        server_name: str,
        server_config: MCPServerConfig,
    ) -> None:
        _validate_server_config(server_name, server_config)
        assert self._exit_stack is not None
        cwd = self._config.resolved_mcp_cwd(server_config)
        timeout = server_config.effective_connect_timeout
        attempt_stack = AsyncExitStack()
        client = None
        try:
            client = await asyncio.wait_for(
                _maybe_await(self._client_factory(server_name, server_config, cwd)),
                timeout=timeout,
            )
            if hasattr(client, "__aenter__"):
                client = await attempt_stack.enter_async_context(client)
            await asyncio.wait_for(_maybe_await(client.initialize()), timeout=timeout)
            response = await asyncio.wait_for(_maybe_await(client.list_tools()), timeout=timeout)
        except Exception:
            client_stack = getattr(client, "_openclaw_exit_stack", None)
            if client_stack is not None:
                await client_stack.aclose()
            await attempt_stack.aclose()
            raise

        client_stack = getattr(client, "_openclaw_exit_stack", None)
        if client_stack is not None:
            self._exit_stack.push_async_callback(client_stack.aclose)
        if getattr(attempt_stack, "_exit_callbacks", None):
            self._exit_stack.push_async_callback(attempt_stack.aclose)
        self._clients[server_name] = client
        for remote_tool in _response_tools(response):
            self._register_tool(server_name, remote_tool)

    async def _stop_async(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._clients.clear()
        self._tools.clear()
        self._errors.clear()

    async def _call_tool_async(self, local_name: str, arguments: dict[str, Any]) -> str:
        tool = self._tools[local_name]
        client = self._clients[tool.server_name]
        timeout = self._config.mcp.servers[tool.server_name].effective_call_timeout
        result = await asyncio.wait_for(
            _maybe_await(client.call_tool(tool.remote_name, arguments)),
            timeout=timeout,
        )
        return _format_call_result(result)

    def _register_tool(self, server_name: str, remote_tool: Any) -> None:
        remote_name = str(_get_attr(remote_tool, "name", ""))
        if not remote_name:
            return
        local_name = _unique_name(
            _local_tool_name(server_name, remote_name),
            set(self._tools),
        )
        description = str(_get_attr(remote_tool, "description", "")) or f"MCP tool {remote_name}"
        schema = _get_attr(remote_tool, "inputSchema", None)
        if schema is None:
            schema = _get_attr(remote_tool, "input_schema", None)
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}, "required": []}
        self._tools[local_name] = MCPTool(
            server_name=server_name,
            remote_name=remote_name,
            local_name=local_name,
            description=description,
            schema=schema,
        )


def _mcp_tool_placeholder(args: dict[str, Any], ctx: ToolContext) -> str:
    return "[error] MCP tools must be executed through MCPManager"


async def _create_stdio_client(
    server_name: str,
    server_config: MCPServerConfig,
    cwd: Path | None,
) -> Any:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise RuntimeError("mcp package is required to use MCP client support") from exc

    env = dict(os.environ)
    env.update(server_config.env)
    params = StdioServerParameters(
        command=server_config.command,
        args=server_config.args,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
    )
    stack = AsyncExitStack()
    read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
    session._openclaw_exit_stack = stack
    return session


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
    raise RuntimeError(f"Unsupported MCP transport for {server_name}: {server_config.transport}")


async def _create_sse_client(
    server_name: str,
    server_config: MCPServerConfig,
    cwd: Path | None,
) -> Any:
    del cwd
    try:
        import httpx
        from mcp import ClientSession
        from mcp.client.sse import sse_client
    except ImportError as exc:
        raise RuntimeError("mcp package with SSE client support is required") from exc

    connect_timeout = server_config.effective_connect_timeout
    read_timeout = _effective_remote_read_timeout(server_config)

    def _make_http_client(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        """自定义 httpx 客户端工厂，强制 HTTP/1.1 以避免 SSE 连接关闭异常。"""
        return httpx.AsyncClient(
            headers=headers,
            timeout=timeout or httpx.Timeout(connect_timeout, read=read_timeout),
            auth=auth,
            http2=False,
            follow_redirects=True,
        )

    stack = AsyncExitStack()
    read_stream, write_stream = await stack.enter_async_context(
        sse_client(
            server_config.url,
            headers=_resolve_headers(server_name, server_config.headers),
            timeout=connect_timeout,
            sse_read_timeout=read_timeout,
            httpx_client_factory=_make_http_client,
        )
    )
    session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
    session._openclaw_exit_stack = stack
    return session


async def _create_streamable_http_client(
    server_name: str,
    server_config: MCPServerConfig,
    cwd: Path | None,
) -> Any:
    del cwd
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client as streamable_http_client
        except ImportError:
            raise RuntimeError("mcp package with Streamable HTTP client support is required") from exc
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx is required for MCP Streamable HTTP client support") from exc

    stack = AsyncExitStack()
    timeout = httpx.Timeout(
        timeout=_effective_remote_read_timeout(server_config),
        connect=server_config.effective_connect_timeout,
    )
    http_client = await stack.enter_async_context(
        httpx.AsyncClient(
            headers=_resolve_headers(server_name, server_config.headers),
            timeout=timeout,
            http2=False,
            follow_redirects=True,
        )
    )
    read_stream, write_stream, _ = await stack.enter_async_context(
        streamable_http_client(
            server_config.url,
            http_client=http_client,
        )
    )
    session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
    session._openclaw_exit_stack = stack
    return session


def _validate_server_config(server_name: str, server_config: MCPServerConfig) -> None:
    if server_config.transport not in {"stdio", "sse", "streamable_http"}:
        raise RuntimeError(f"Unsupported MCP transport for {server_name}: {server_config.transport}")
    if server_config.transport == "stdio" and not server_config.command:
        raise RuntimeError(f"MCP stdio server {server_name} requires command")
    if server_config.transport in {"sse", "streamable_http"} and not server_config.url:
        raise RuntimeError(f"MCP {server_config.transport} server {server_name} requires url")


def _effective_remote_read_timeout(server_config: MCPServerConfig) -> int | float | None:
    if server_config.read_timeout is not None:
        return server_config.read_timeout
    return None


def _resolve_headers(server_name: str, headers: dict[str, str]) -> dict[str, str]:
    resolved = {}
    for key, value in headers.items():
        resolved[key] = _expand_env_placeholders(server_name, key, value)
    return resolved


def _expand_env_placeholders(server_name: str, header_name: str, value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        env_value = os.environ.get(env_name)
        if env_value is None:
            raise RuntimeError(
                f"Missing environment variable {env_name} for MCP server {server_name} header {header_name}"
            )
        return env_value

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)


def _local_tool_name(server_name: str, remote_name: str) -> str:
    server = _sanitize_name(server_name)
    remote = _sanitize_name(remote_name)
    return f"{server}__{remote}"[:64].rstrip("_")


def _sanitize_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    cleaned = cleaned.strip("_")
    return cleaned or "tool"


def _unique_name(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    for index in range(2, 1000):
        suffix = f"_{index}"
        candidate = f"{base[:64 - len(suffix)]}{suffix}"
        if candidate not in existing:
            return candidate
    raise RuntimeError(f"Could not allocate unique MCP tool name for {base}")


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value) or hasattr(value, "__await__"):
        return await value
    return value


def _response_tools(response: Any) -> list[Any]:
    tools = _get_attr(response, "tools", response)
    return list(tools or [])


def _format_call_result(result: Any) -> str:
    is_error = bool(_get_attr(result, "isError", False) or _get_attr(result, "is_error", False))
    content = _get_attr(result, "content", result)
    if isinstance(content, list):
        parts = [_format_content_item(item) for item in content]
        text = "\n".join(part for part in parts if part)
    else:
        text = _format_content_item(content)
    return f"[error] {text}" if is_error else text


def _format_content_item(item: Any) -> str:
    text = _get_attr(item, "text", None)
    if text is not None:
        return str(text)
    if isinstance(item, str):
        return item
    if isinstance(item, bytes):
        return item.decode("utf-8", errors="replace")
    summary = _summarize_non_text_content(item)
    if summary is not None:
        return json.dumps(summary, ensure_ascii=False)
    if hasattr(item, "model_dump"):
        return json.dumps(item.model_dump(), ensure_ascii=False)
    if isinstance(item, dict):
        return json.dumps(item, ensure_ascii=False)
    return str(item)


def _summarize_non_text_content(item: Any) -> dict[str, Any] | None:
    content_type = _get_attr(item, "type", None)
    if content_type in (None, "text"):
        return None
    summary: dict[str, Any] = {"type": content_type}
    for key in ("mimeType", "mime_type", "uri", "name", "description"):
        value = _get_attr(item, key, None)
        if value is not None:
            summary[key] = value
    return summary


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
