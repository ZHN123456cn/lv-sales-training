from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import yaml

import agent.loop as loop_module
from agent.cli import CLI
from agent.config import AgentConfig, MCPServerConfig, load_config
from agent.llm import _BlockingLLMResponse
from agent.mcp import (
    MCPManager,
    MCPTool,
    _effective_remote_read_timeout,
    _local_tool_name,
    _resolve_headers,
)
from agent.session import Session, SessionManager
from tests.conftest import make_blocking_completion


def blocking_resp(content: str, tool_calls=None) -> _BlockingLLMResponse:
    return _BlockingLLMResponse(make_blocking_completion(content, tool_calls))


def tool_call_resp(tool_id: str, tool_name: str, args_json: str) -> _BlockingLLMResponse:
    tc = [{"id": tool_id, "name": tool_name, "arguments": args_json}]
    return _BlockingLLMResponse(make_blocking_completion("", tool_calls=tc))


class FakeMCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def initialize(self) -> None:
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=[
            SimpleNamespace(
                name="read-file",
                description="Read a file from MCP.",
                inputSchema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            )
        ])

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return SimpleNamespace(content=[SimpleNamespace(text=f"{name}:{arguments['path']}")])


def test_load_config_reads_mcp_servers(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.config.PROJECT_ROOT", tmp_path)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump({
        "mcp": {
            "enabled": True,
            "servers": {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                    "cwd": ".",
                    "env": {"TOKEN": "x"},
                    "timeout": 12,
                }
            },
        }
    }), encoding="utf-8")

    cfg = load_config(cfg_path)

    assert cfg.mcp.enabled is True
    server = cfg.mcp.servers["filesystem"]
    assert server.transport == "stdio"
    assert server.command == "npx"
    assert server.args[-1] == "."
    assert server.env == {"TOKEN": "x"}
    assert server.timeout == 12
    assert cfg.resolved_mcp_cwd(server) == tmp_path.resolve()


def test_load_config_reads_remote_mcp_servers(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.config.PROJECT_ROOT", tmp_path)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump({
        "mcp": {
            "enabled": True,
            "servers": {
                "docs": {
                    "transport": "sse",
                    "url": "https://example.com/sse",
                    "headers": {"Authorization": "Bearer ${TOKEN}"},
                    "timeout": 20,
                    "connect_timeout": 5,
                    "read_timeout": 300,
                    "call_timeout": 60,
                    "max_retries": 3,
                    "retry_backoff_seconds": 0.25,
                },
                "search": {
                    "transport": "streamable_http",
                    "url": "https://example.com/mcp",
                },
            },
        }
    }), encoding="utf-8")

    cfg = load_config(cfg_path)

    docs = cfg.mcp.servers["docs"]
    assert docs.transport == "sse"
    assert docs.url == "https://example.com/sse"
    assert docs.headers == {"Authorization": "Bearer ${TOKEN}"}
    assert docs.timeout == 20
    assert docs.effective_connect_timeout == 5
    assert docs.effective_read_timeout == 300
    assert docs.effective_call_timeout == 60
    assert docs.max_retries == 3
    assert docs.retry_backoff_seconds == 0.25
    assert cfg.mcp.servers["search"].transport == "streamable_http"
    assert cfg.mcp.servers["search"].url == "https://example.com/mcp"


def test_remote_header_env_interpolation(monkeypatch):
    monkeypatch.setenv("TOKEN", "secret")

    headers = _resolve_headers("docs", {"Authorization": "Bearer ${TOKEN}"})

    assert headers == {"Authorization": "Bearer secret"}


def test_remote_read_timeout_defaults_to_no_idle_timeout():
    server = MCPServerConfig(transport="sse", url="https://example.com/sse", timeout=30)

    assert _effective_remote_read_timeout(server) is None


def test_remote_read_timeout_respects_explicit_value():
    server = MCPServerConfig(
        transport="sse",
        url="https://example.com/sse",
        timeout=30,
        read_timeout=300,
    )

    assert _effective_remote_read_timeout(server) == 300


def test_remote_header_missing_env_records_server_error(tmp_path, monkeypatch):
    monkeypatch.delenv("TOKEN", raising=False)
    cfg = AgentConfig()
    cfg.project_root = tmp_path
    cfg.mcp.enabled = True
    cfg.mcp.servers["docs"] = MCPServerConfig(
        transport="sse",
        url="https://example.com/sse",
        headers={"Authorization": "Bearer ${TOKEN}"},
        max_retries=0,
    )

    def factory(server_name, server_config, _cwd):
        _resolve_headers(server_name, server_config.headers)
        return FakeMCPClient()

    manager = MCPManager(cfg, client_factory=factory)
    manager.start()
    try:
        assert "docs" in manager.errors
        assert "TOKEN" in manager.errors["docs"]
        assert "Authorization" in manager.errors["docs"]
    finally:
        manager.stop()


def test_local_tool_name_sanitizes_and_limits_length():
    name = _local_tool_name("my server!", "tool/name " + "x" * 80)
    assert name.startswith("my_server__tool_name")
    assert len(name) <= 64
    assert "/" not in name


def test_mcp_manager_lists_and_calls_fake_tool(tmp_path):
    cfg = AgentConfig()
    cfg.project_root = tmp_path
    cfg.mcp.enabled = True
    from agent.config import MCPServerConfig
    cfg.mcp.servers["fs"] = MCPServerConfig(command="fake", timeout=5)
    client = FakeMCPClient()

    manager = MCPManager(cfg, client_factory=lambda *_: client)
    manager.start()
    try:
        tool_defs = manager.list_tool_defs()
        assert [td.name for td in tool_defs] == ["fs__read-file"]
        schema = tool_defs[0].to_openai_schema()
        assert schema["function"]["parameters"]["properties"]["path"]["type"] == "string"

        result = manager.call_tool("fs__read-file", {"path": "a.txt"})
        assert result == "read-file:a.txt"
        assert client.calls == [("read-file", {"path": "a.txt"})]
    finally:
        manager.stop()


def test_mcp_manager_retries_startup_failures(tmp_path):
    cfg = AgentConfig()
    cfg.project_root = tmp_path
    cfg.mcp.enabled = True
    cfg.mcp.servers["fs"] = MCPServerConfig(
        command="fake",
        timeout=5,
        max_retries=2,
        retry_backoff_seconds=0,
    )
    client = FakeMCPClient()
    attempts = {"count": 0}

    def factory(*_):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("temporary")
        return client

    manager = MCPManager(cfg, client_factory=factory)
    manager.start()
    try:
        assert attempts["count"] == 3
        assert manager.errors == {}
        assert [td.name for td in manager.list_tool_defs()] == ["fs__read-file"]
    finally:
        manager.stop()


def test_mcp_manager_does_not_retry_tool_calls(tmp_path):
    cfg = AgentConfig()
    cfg.project_root = tmp_path
    cfg.mcp.enabled = True
    cfg.mcp.servers["fs"] = MCPServerConfig(command="fake", timeout=5)

    class FailingCallClient(FakeMCPClient):
        async def call_tool(self, name: str, arguments: dict):
            self.calls.append((name, arguments))
            raise RuntimeError("boom")

    client = FailingCallClient()
    manager = MCPManager(cfg, client_factory=lambda *_: client)
    manager.start()
    try:
        result = manager.call_tool("fs__read-file", {"path": "a.txt"})
        assert result == "[error] MCP tool fs__read-file failed: boom"
        assert client.calls == [("read-file", {"path": "a.txt"})]
    finally:
        manager.stop()


def test_agent_loop_dispatches_mcp_tool(monkeypatch, default_config: AgentConfig, empty_session: Session):
    default_config.mcp.enabled = True
    default_config.mcp.servers = {"fs": object()}

    class FakeManager:
        def __init__(self, config):
            self.config = config

        def start(self):
            return None

        def stop(self):
            return None

        def list_tool_defs(self):
            from agent.tools import ToolDef
            return [ToolDef(
                name="fs__read_file",
                description="Read through MCP.",
                schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                func=lambda args, ctx: "unused",
            )]

        def call_tool(self, name, arguments):
            return f"mcp-result:{name}:{arguments['path']}"

    monkeypatch.setattr(loop_module, "MCPManager", FakeManager)
    llm = MagicMock()
    llm.chat_blocking.side_effect = [
        tool_call_resp("mcp1", "fs__read_file", '{"path": "README.md"}'),
        blocking_resp("done"),
    ]

    agent_loop = loop_module.AgentLoop(default_config, empty_session, llm)
    result = agent_loop.run("use mcp")

    assert result == "done"
    tool_messages = [m for m in empty_session.messages if m["role"] == "tool"]
    assert tool_messages[0]["content"] == "mcp-result:fs__read_file:README.md"
    sent_tools = llm.chat_blocking.call_args_list[0].kwargs["tools"]
    assert any(t["function"]["name"] == "fs__read_file" for t in sent_tools)


def test_cli_mcp_list_groups_tools_by_server(tmp_path, capsys):
    cfg = AgentConfig()
    cfg.mcp.enabled = True
    cfg.mcp.servers = {
        "hello-world": MCPServerConfig(
            command="python",
            args=["server.py"],
            cwd="C:/tmp/mcp",
        ),
        "empty": MCPServerConfig(command="node"),
    }
    session = Session(id="s1", created_at="now", updated_at="now")
    cli = CLI(cfg, session, SessionManager(tmp_path))
    cli._loop = SimpleNamespace(
        mcp_server_tools={
            "hello-world": [
                MCPTool(
                    server_name="hello-world",
                    remote_name="say_hello",
                    local_name="hello-world__say_hello",
                    description="Say hello.",
                    schema={"type": "object", "properties": {}},
                )
            ]
        },
        mcp_server_errors={},
    )

    cli._handle_slash("/mcp list")

    output = capsys.readouterr().out
    assert "hello-world" in output
    assert "transport: stdio" in output
    assert "hello-world__say_hello -> say_hello" in output
    assert "empty" in output
    assert "none discovered" in output


def test_cli_mcp_list_shows_remote_url_but_not_headers(tmp_path, capsys):
    cfg = AgentConfig()
    cfg.mcp.enabled = True
    cfg.mcp.servers = {
        "remote-docs": MCPServerConfig(
            transport="sse",
            url="https://example.com/sse",
            headers={"Authorization": "Bearer secret-token"},
        ),
    }
    session = Session(id="s1", created_at="now", updated_at="now")
    cli = CLI(cfg, session, SessionManager(tmp_path))
    cli._loop = SimpleNamespace(
        mcp_server_tools={},
        mcp_server_errors={},
    )

    cli._handle_slash("/mcp list")

    output = capsys.readouterr().out
    assert "remote-docs" in output
    assert "transport: sse" in output
    assert "url: https://example.com/sse" in output
    assert "Authorization" not in output
    assert "secret-token" not in output
