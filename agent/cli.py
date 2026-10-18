"""
交互式 CLI：REPL 主循环。

功能：
- 彩色输出（工具调用灰色、错误红色、系统提示蓝色）
- 斜杠命令（/help /exit /session /history /clear /tools /cd /config）
- readline 历史（跨会话持久化）
- 流式输出最终回答
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .config import AgentConfig
from .loop import AgentLoop, ToolCallEvent, ToolResultEvent
from .session import Session, SessionManager
from .skills import SkillRegistry, read_skill_markdown
from .tools import TOOL_REGISTRY

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# 颜色工具（使用 colorama，降级为无色）
# ---------------------------------------------------------------------------

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)
    _HAS_COLOR = True
except ImportError:
    _HAS_COLOR = False

    class _NoColor:
        def __getattr__(self, _: str) -> str:
            return ""

    Fore = _NoColor()   # type: ignore
    Style = _NoColor()  # type: ignore


def _gray(text: str) -> str:
    return f"{Fore.WHITE}{Style.DIM}{text}{Style.RESET_ALL}" if _HAS_COLOR else text

def _red(text: str) -> str:
    return f"{Fore.RED}{text}{Style.RESET_ALL}" if _HAS_COLOR else text

def _blue(text: str) -> str:
    return f"{Fore.CYAN}{text}{Style.RESET_ALL}" if _HAS_COLOR else text

def _green(text: str) -> str:
    return f"{Fore.GREEN}{text}{Style.RESET_ALL}" if _HAS_COLOR else text

def _yellow(text: str) -> str:
    return f"{Fore.YELLOW}{text}{Style.RESET_ALL}" if _HAS_COLOR else text

def _bold(text: str) -> str:
    return f"{Style.BRIGHT}{text}{Style.RESET_ALL}" if _HAS_COLOR else text


# ---------------------------------------------------------------------------
# readline 历史
# ---------------------------------------------------------------------------

def _setup_readline(history_path: Path) -> None:
    try:
        import readline as rl
        history_path.parent.mkdir(parents=True, exist_ok=True)
        if history_path.exists():
            rl.read_history_file(str(history_path))
        rl.set_history_length(1000)

        # Tab 补全斜杠命令
        SLASH_COMMANDS = [
            "/help", "/exit", "/quit",
            "/session new", "/session list", "/session load ",
            "/session info", "/session delete ",
            "/agent list", "/agent create ", "/agent ",
            "/history", "/clear", "/tools", "/config",
            "/skills", "/skill ", "/memory", "/mcp list",
            "/training",
        ]
        def completer(text: str, state: int) -> str | None:
            options = [c for c in SLASH_COMMANDS if c.startswith(text)]
            return options[state] if state < len(options) else None

        rl.set_completer(completer)
        rl.parse_and_bind("tab: complete")

        import atexit
        atexit.register(rl.write_history_file, str(history_path))
    except (ImportError, OSError):
        pass  # Windows 或无 readline 时静默忽略


# ---------------------------------------------------------------------------
# CLI 主类
# ---------------------------------------------------------------------------

VERSION = "0.1.0"

HELP_TEXT = """
可用命令：  
  /help                显示此帮助
  /exit, /quit         退出程序
  /session new         创建新 Session
  /session list        列出所有 Session
  /session load <id>   切换到指定 Session
  /session info        显示当前 Session 信息
  /session delete <id> 删除指定 Session
  /agent list          列出所有 Agent
  /agent create <name> 创建 Agent workspace（不切换）
  /agent <name>        切换到指定 Agent
  /history             显示当前对话历史（摘要）
  /clear               清空当前对话历史（不删除文件）
  /tools               列出所有可用工具
  /skills              列出所有可用 Skill
  /skill <name>        显示指定 Skill 的 SKILL.md 路径和摘要
  /mcp list            List MCP servers, transports, tools, and startup errors.
  /memory show         显示当前长期记忆文件内容（/memory 等价于 show）
  /memory daily [date] 显示每日记忆；不传 date 时读取今天
  /memory search <q>   搜索长期记忆与每日记忆
  /memory status       显示 FTS5 / sqlite-vec / embedding 状态
  /memory reindex      从 Markdown 真源强制重建 FTS5 与向量索引
  /memory promote <id>
                       将指定每日记忆晋升到长期记忆
  /config              显示当前配置
  /cd <path>           切换工作目录
  /training            启动 LV 销售训练完整闭环（选场景→对话→教练评估）

直接输入内容即向 Agent 发送消息。
输入"开始培训"也可直接启动训练流程。
按 Ctrl+C 或 Ctrl+D 退出。
""".strip()


class CLI:
    def __init__(
        self,
        config: AgentConfig,
        session: Session,
        session_mgr: SessionManager,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self._config = config
        self._session = session
        self._session_mgr = session_mgr
        self._skill_registry = skill_registry
        self._loop: AgentLoop | None = None
        self._running = True

    def _reset_loop(self, reload_skills: bool = False) -> None:
        """Session 切换后重建 loop。"""
        from .llm import LLMClient
        from .skills import load_skills
        if self._loop is not None:
            self._loop.close()
        if reload_skills or self._skill_registry is None:
            self._skill_registry = load_skills(self._config)
        llm = LLMClient(self._config.llm)
        self._loop = AgentLoop(self._config, self._session, llm, self._skill_registry)

    def run(self) -> None:
        """进入交互式 REPL。"""
        history_path = self._config.resolved_session_dir() / ".input_history"
        _setup_readline(history_path)
        self._reset_loop()

        self._print_banner()

        while self._running:
            try:
                user_input = input(_bold("> ")).strip()
            except KeyboardInterrupt:
                print()
                self._sys_print("按 Ctrl+D 退出，或输入 /exit")
                continue
            except EOFError:
                print()
                self._sys_print("再见！")
                break

            if not user_input:
                continue

            if user_input.startswith("/"):
                self._handle_slash(user_input)
            else:
                self._handle_message(user_input)

        if self._loop is not None:
            self._loop.close()

    # ------------------------------------------------------------------
    # 斜杠命令处理
    # ------------------------------------------------------------------

    def _handle_slash(self, cmd: str) -> None:
        parts = cmd.split(maxsplit=2)
        verb = parts[0].lower()

        if verb in ("/exit", "/quit"):
            self._sys_print("再见！")
            self._running = False

        elif verb == "/help":
            print(HELP_TEXT)

        elif verb == "/session":
            self._handle_session_cmd(parts[1:] if len(parts) > 1 else [])

        elif verb == "/agent":
            self._handle_agent_cmd(parts[1:] if len(parts) > 1 else [])

        elif verb == "/history":
            self._print_history()

        elif verb == "/clear":
            self._clear_history()

        elif verb == "/tools":
            self._print_tools()

        elif verb == "/skills":
            self._print_skills()

        elif verb == "/skill":
            if len(parts) < 2:
                self._err_print("用法：/skill <name>")
            else:
                self._print_skill(parts[1])

        elif verb == "/config":
            self._print_config()

        elif verb == "/memory":
            self._handle_memory_cmd(parts[1:] if len(parts) > 1 else [])

        elif verb == "/mcp":
            self._handle_mcp_cmd(parts[1:] if len(parts) > 1 else [])

        elif verb == "/cd":
            if len(parts) < 2:
                self._err_print("用法：/cd <路径>")
            else:
                self._change_dir(parts[1])

        elif verb == "/training":
            self._start_training()

        else:
            self._err_print(f"未知命令：{verb}（输入 /help 查看可用命令）")

    def _start_training(self) -> None:
        """启动 LV 销售训练完整闭环（委托给子目录 venv 运行，避免包冲突）。"""
        import subprocess
        sub_dir = self._config.project_root / "project_class_all_ragplus"
        py_exe = sub_dir / ".venv" / "Scripts" / "python.exe"
        script = sub_dir / "scripts" / "run_training.py"
        if not py_exe.exists():
            self._err_print(f"训练环境不存在：{py_exe}\n请先在 {sub_dir} 下创建虚拟环境。")
            return
        if not script.exists():
            self._err_print(f"训练脚本不存在：{script}")
            return
        self._sys_print("正在启动训练编排器（子目录环境）...")
        try:
            subprocess.run([str(py_exe), str(script)], cwd=str(sub_dir))
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            self._err_print(f"训练启动失败：{exc}")

    def _handle_session_cmd(self, args: list[str]) -> None:
        if not args:
            self._err_print("用法：/session new | list | load <id> | info | delete <id>")
            return

        sub = args[0].lower()

        if sub == "new":
            self._new_session()

        elif sub == "list":
            self._list_sessions()

        elif sub == "load":
            if len(args) < 2:
                self._err_print("用法：/session load <session-id>")
            else:
                self._load_session(args[1])

        elif sub == "info":
            self._session_info()

        elif sub == "delete":
            if len(args) < 2:
                self._err_print("用法：/session delete <session-id>")
            else:
                self._delete_session(args[1])

        else:
            self._err_print(f"未知 session 子命令：{sub}")

    def _handle_agent_cmd(self, args: list[str]) -> None:
        if not args:
            self._err_print("用法：/agent list | create <name> | <name>")
            return

        sub = args[0].lower()
        if sub == "list":
            self._list_agents()
        elif sub == "create":
            if len(args) < 2:
                self._err_print("用法：/agent create <name>")
            else:
                self._create_agent(args[1])
        else:
            self._switch_agent(args[0])

    # ------------------------------------------------------------------
    # Agent 消息处理
    # ------------------------------------------------------------------

    def _handle_message(self, user_input: str) -> None:
        # 自然语言触发训练流程
        training_keywords = ["开始培训", "启动培训", "我要培训", "开始训练", "启动训练", "销售培训", "开始销售培训"]
        if any(kw in user_input for kw in training_keywords):
            self._start_training()
            return

        print()
        try:
            # 流式输出最终回答
            started = False
            for token in self._loop.stream_run(
                user_input,
                on_tool_call=self._on_tool_call,
                on_tool_result=self._on_tool_result,
            ):
                if not started:
                    started = True
                sys.stdout.write(token)
                sys.stdout.flush()

            if started:
                print("\n")

        except KeyboardInterrupt:
            print()
            self._sys_print("已中断。")
        except Exception as e:
            self._err_print(f"Agent 出错：{e}")

        # 自动保存
        if self._config.session.auto_save:
            try:
                self._session_mgr.save_session(self._session)
            except Exception as e:
                self._err_print(f"Session 保存失败：{e}")

    def _on_tool_call(self, event: ToolCallEvent) -> None:
        import json as _json
        args_str = _json.dumps(event.arguments, ensure_ascii=False)
        if len(args_str) > 120:
            args_str = args_str[:120] + "..."
        prefix = "[读取 Skill]" if event.name == "skill_read" else "[调用工具]"
        print(_gray(f"{prefix} {event.name}  {args_str}"))

    def _on_tool_result(self, event: ToolResultEvent) -> None:
        result = event.result
        if len(result) > 200:
            result = result[:200] + "..."
        print(_gray(f"[工具结果] {result}"))
        print()

    # ------------------------------------------------------------------
    # Session 操作
    # ------------------------------------------------------------------

    def _new_session(self) -> None:
        # 先保存当前
        self._session_mgr.save_session(self._session)
        self._session = self._session_mgr.new_session(
            work_dir=str(self._loop.tool_ctx.work_dir)
        )
        self._reset_loop()
        self._sys_print(f"已创建新 Session：{self._session.id}")

    def _list_sessions(self) -> None:
        metas = self._session_mgr.list_sessions()
        if not metas:
            self._sys_print("暂无 Session 记录。")
            return
        print(_blue("Session 列表（最近在前）："))
        for m in metas:
            marker = " ← 当前" if m.id == self._session.id else ""
            title = m.title or "(无标题)"
            print(f"  {_yellow(m.id)}  {title[:30]}  {m.updated_at}{marker}")

    def _load_session(self, session_id: str) -> None:
        try:
            self._session_mgr.save_session(self._session)
            self._session = self._session_mgr.load_session(session_id)
            self._reset_loop()
            title = self._session.title or "(无标题)"
            self._sys_print(f"已切换到 Session：{session_id}（{title}）")
        except FileNotFoundError:
            self._err_print(f"Session 不存在：{session_id}")

    def _latest_or_new_session(self) -> Session:
        session = self._session_mgr.get_latest_session()
        if session is not None:
            return session
        return self._session_mgr.new_session(work_dir=str(self._config.resolved_work_dir()))

    def _session_info(self) -> None:
        s = self._session
        msg_count = len([m for m in s.messages if m.get("role") in ("user", "assistant")])
        print(_blue("当前 Session 信息："))
        print(f"  ID：      {s.id}")
        print(f"  标题：    {s.title or '(无标题)'}")
        print(f"  创建时间：{s.created_at}")
        print(f"  更新时间：{s.updated_at}")
        print(f"  工作目录：{self._loop.tool_ctx.work_dir}")
        print(f"  消息轮数：{msg_count // 2}")

    def _delete_session(self, session_id: str) -> None:
        if session_id == self._session.id:
            self._err_print("不能删除当前正在使用的 Session，请先切换到其他 Session。")
            return
        ok = self._session_mgr.delete_session(session_id)
        if ok:
            self._sys_print(f"已删除 Session：{session_id}")
        else:
            self._err_print(f"Session 不存在：{session_id}")

    # ------------------------------------------------------------------
    # 其他命令
    # ------------------------------------------------------------------

    def _print_history(self) -> None:
        messages = self._session.messages
        user_msgs = [(i, m) for i, m in enumerate(messages) if m.get("role") == "user"]
        if not user_msgs:
            self._sys_print("当前对话历史为空。")
            return
        print(_blue(f"当前对话历史（共 {len(user_msgs)} 轮）："))
        for turn, (_, m) in enumerate(user_msgs, 1):
            content = m.get("content", "")
            if isinstance(content, str):
                preview = content[:60].replace("\n", " ")
                print(f"  [{turn}] {preview}")

    def _clear_history(self) -> None:
        self._session.messages.clear()
        self._sys_print("已清空当前对话历史（Session 文件未删除）。")

    def _print_tools(self) -> None:
        enabled = self._loop.enabled_tools
        print(_blue("可用工具："))
        for name, td in TOOL_REGISTRY.items():
            status = _green("启用") if name in enabled else _gray("禁用")
            print(f"  [{status}] {_bold(name)} — {td.description}")

        if self._loop.mcp_tools:
            print(_blue("MCP tools:"))
            for td in self._loop.mcp_tools:
                print(f"  [{_green('enabled')}] {_bold(td.name)} - {td.description}")

    def _print_skills(self) -> None:
        skills = self._loop.skill_registry.list_for_prompt()
        if not skills:
            self._sys_print("暂无可用 Skill。")
            return
        print(_blue("可用 Skill："))
        for skill in skills:
            print(f"  {_bold(skill.name)} — {skill.description}")

    def _print_skill(self, name: str) -> None:
        skill = self._loop.skill_registry.get(name)
        if skill is None:
            self._err_print(f"Skill 不存在：{name}")
            return
        print(_blue(f"Skill：{skill.name}"))
        print(f"  描述：{skill.description}")
        print(f"  路径：{skill.file_path}")
        preview = read_skill_markdown(skill.name, self._loop.skill_registry)
        if len(preview) > 1200:
            preview = preview[:1200] + "..."
        print(_gray(preview))

    def _print_config(self) -> None:
        cfg = self._config
        print(_blue("当前配置："))
        print(f"  Agent：     {cfg.agent_scope.id if cfg.agent_scope else 'main'}")
        print(f"  模型：      {cfg.llm.model}")
        print(f"  API Base：  {cfg.llm.base_url}")
        print(f"  Provider：  {cfg.llm.provider}")
        print(f"  温度：      {cfg.llm.temperature}")
        print(f"  流式输出：  {cfg.llm.streaming}")
        print(f"  最大迭代：  {cfg.max_iterations}")
        print(f"  工作目录：  {cfg.resolved_work_dir()}")
        print(f"  Session 目录：{cfg.resolved_session_dir()}")
        print(f"  Memory 启用： {cfg.memory.enabled}")
        print(f"  Memory 文件： {cfg.resolved_memory_file()}")
        if self._loop.memory_store is not None:
            status = self._loop.memory_store.index_status()
            print(f"  Memory FTS5： {status['fts5_enabled']}")
            print(f"  Memory sqlite-vec： {status['sqlite_vec_enabled']}")
        print(f"  配置文件：  {cfg.config_path}")

    def _list_agents(self) -> None:
        from .agents import list_agents

        agents = list_agents(self._config.project_root)
        if not agents:
            self._sys_print("暂无 Agent。")
            return
        current = self._config.agent_scope.id if self._config.agent_scope else "main"
        print(_blue("Agent 列表："))
        for agent in agents:
            marker = " ← 当前" if agent.id == current else ""
            print(f"  {_yellow(agent.id)}  {agent.workspace_dir}{marker}")

    def _create_agent(self, name: str) -> None:
        from .agents import ensure_agent_workspace, normalize_agent_id, scope_for

        try:
            agent_id = normalize_agent_id(name)
        except ValueError as e:
            self._err_print(str(e))
            return
        scope = scope_for(self._config.project_root, agent_id)
        existed = scope.workspace_dir.exists()
        ensure_agent_workspace(scope)
        if existed:
            self._sys_print(f"Agent 已存在，已补齐内部结构：{agent_id} -> {scope.workspace_dir}")
        else:
            self._sys_print(f"已创建 Agent：{agent_id} -> {scope.workspace_dir}")

    def _switch_agent(self, name: str) -> None:
        from .agents import (
            ensure_agent_workspace_contents,
            normalize_agent_id,
            read_identity,
            scope_for,
            workspace_exists,
        )

        try:
            agent_id = normalize_agent_id(name)
        except ValueError as e:
            self._err_print(str(e))
            return
        current = self._config.agent_scope.id if self._config.agent_scope else "main"
        if agent_id == current:
            self._sys_print(f"已经在 Agent：{agent_id}")
            return
        if not workspace_exists(self._config.project_root, agent_id):
            self._err_print(f"Agent 不存在：{agent_id}。请先使用 /agent create {agent_id}")
            return

        try:
            if self._config.session.auto_save:
                self._session_mgr.save_session(self._session)
            if self._loop is not None:
                self._loop.close()

            scope = scope_for(self._config.project_root, agent_id)
            ensure_agent_workspace_contents(scope)
            self._config.agent_scope = scope
            self._config.identity_text = read_identity(scope)
            self._session_mgr = SessionManager(self._config.resolved_session_dir())
            self._session = self._latest_or_new_session()
            self._reset_loop(reload_skills=True)
            self._sys_print(
                f"已切换到 Agent：{agent_id}；workspace：{scope.workspace_dir}；Session：{self._session.id}"
            )
        except Exception as e:
            self._err_print(f"切换 Agent 失败：{e}")

    def _handle_memory_cmd(self, args: list[str]) -> None:
        store = self._loop.memory_store
        if store is None:
            self._err_print("Memory 系统未启用")
            return
        sub = args[0].lower() if args else "show"
        if sub == "show":
            self._print_memory()
        elif sub == "daily":
            day = args[1] if len(args) > 1 else None
            print(_blue(f"Daily memory：{day or 'today'}"))
            print(store.read_daily_text(day))
        elif sub == "search":
            if len(args) < 2:
                self._err_print("用法：/memory search <query>")
                return
            from .memory import format_search_results
            import json as _json
            print(_json.dumps(format_search_results(store.search(" ".join(args[1:]))), ensure_ascii=False, indent=2))
        elif sub == "reindex":
            store.rebuild_index()
            self._sys_print("Memory 索引已重建")
        elif sub == "status":
            import json as _json
            print(_json.dumps(store.index_status(), ensure_ascii=False, indent=2))
        elif sub == "promote":
            if len(args) < 2:
                self._err_print("用法：/memory promote <daily-item-id>")
                return
            try:
                promoted_id = store.promote_daily(args[1], "CLI manual promotion")
                self._sys_print(f"已晋升为长期记忆：{promoted_id}")
            except Exception as e:
                self._err_print(f"晋升失败：{e}")
        else:
            self._err_print("用法：/memory show | daily [YYYY-MM-DD] | search <query> | status | reindex | promote <id>")

    def _handle_mcp_cmd(self, args: list[str]) -> None:
        sub = args[0].lower() if args else "list"
        if sub != "list":
            self._err_print("Usage: /mcp list")
            return
        self._print_mcp_servers()

    def _print_mcp_servers(self) -> None:
        servers = self._config.mcp.servers
        print(_blue("MCP servers:"))
        if not servers:
            self._sys_print("No MCP servers configured.")
            return

        tools_by_server = self._loop.mcp_server_tools
        errors_by_server = self._loop.mcp_server_errors
        for name, server in servers.items():
            state = "enabled" if self._config.mcp.enabled else "disabled"
            print(f"  {_bold(name)} [{state}]")
            print(f"    transport: {server.transport}")
            if server.transport == "stdio":
                print(f"    command: {server.command}")
                if server.args:
                    print(f"    args: {' '.join(server.args)}")
                if server.cwd:
                    print(f"    cwd: {server.cwd}")
            else:
                print(f"    url: {server.url}")

            tools = tools_by_server.get(name, [])
            if not self._config.mcp.enabled:
                print("    tools: (MCP disabled)")
            elif name in errors_by_server:
                print(f"    error: {errors_by_server[name]}")
                print("    tools: (none discovered)")
            elif not tools:
                print("    tools: (none discovered)")
            else:
                print("    tools:")
                for tool in tools:
                    print(f"      - {tool.local_name} -> {tool.remote_name}: {tool.description}")

    def _print_memory(self) -> None:
        store = self._loop.memory_store
        if store is None:
            self._err_print("Memory 系统未启用")
            return
        print(_blue(f"Memory：{store.path}"))
        print(store.read_long_term_text())

    def _change_dir(self, path: str) -> None:
        from .tools import execute_tool
        result = execute_tool("change_dir", {"path": path}, self._loop.tool_ctx)
        self._sys_print(result)

    # ------------------------------------------------------------------
    # 打印工具方法
    # ------------------------------------------------------------------

    def _sys_print(self, msg: str) -> None:
        print(_blue(f"[系统] {msg}"))

    def _err_print(self, msg: str) -> None:
        print(_red(f"[错误] {msg}"))

    def _print_banner(self) -> None:
        title = self._session.title or "(新对话)"
        work_dir = self._loop.tool_ctx.work_dir
        print(_bold(f"\nAgent v{VERSION}"))
        print(_gray(f"Agent：{self._config.agent_scope.id if self._config.agent_scope else 'main'}"))
        print(_gray(f"Session：{self._session.id}"))
        print(_gray(f"工作目录：{work_dir}"))
        print(_gray(f"模型：{self._config.llm.model}"))
        print(_gray("输入 /help 查看可用命令\n"))
