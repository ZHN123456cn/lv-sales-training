"""
程序入口。

用法：
  python main.py                           # 启动新会话（交互模式）
  python main.py --resume                  # 恢复最近会话
  python main.py --session <id>            # 恢复指定会话
  python main.py --message "你好"          # 单条消息模式（非交互）
  python main.py --agent work              # 启动指定 Agent
  python main.py --config /path/config.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent",
        description="Python ReAct Agent CLI",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        metavar="PATH",
        help="配置文件路径（默认：project_class/config.yaml）",
    )
    parser.add_argument(
        "--session",
        type=str,
        default=None,
        metavar="SESSION_ID",
        help="恢复指定 Session ID 的对话历史",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="恢复最近一次 Session",
    )
    parser.add_argument(
        "--message", "-m",
        type=str,
        default=None,
        metavar="TEXT",
        help="直接发送单条消息并退出（非交互模式）",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="禁用流式输出（覆盖配置文件设置）",
    )
    parser.add_argument(
        "--agent",
        type=str,
        default=None,
        metavar="NAME",
        help="启动指定 Agent（默认：main）",
    )
    return parser.parse_args()


def setup_agent_scope(config, agent_arg: str | None, interactive: bool) -> None:
    """解析并绑定当前 AgentScope。"""
    import os
    from agent.agents import (
        ensure_agent_workspace,
        ensure_agent_workspace_contents,
        normalize_agent_id,
        read_identity,
        scope_for,
        workspace_exists,
    )

    raw_agent = agent_arg or os.environ.get("AGENT_ID") or config.agents.default or "main"
    try:
        agent_id = normalize_agent_id(raw_agent)
    except ValueError as exc:
        print(f"[错误] Agent 名称非法：{exc}", file=sys.stderr)
        sys.exit(1)

    scope = scope_for(config.project_root, agent_id)
    if not workspace_exists(config.project_root, agent_id):
        if not interactive:
            print(
                f"[错误] Agent 不存在：{agent_id}（workspace：{scope.workspace_dir}）",
                file=sys.stderr,
            )
            sys.exit(1)
        answer = input(f"[系统] Agent '{agent_id}' 不存在，是否创建？[y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print(f"[错误] Agent 不存在：{agent_id}", file=sys.stderr)
            sys.exit(1)
        ensure_agent_workspace(scope)
    else:
        ensure_agent_workspace_contents(scope)

    config.agent_scope = scope
    config.identity_text = read_identity(scope)


def resolve_session(args, session_mgr, config):
    """根据命令行参数决定使用哪个 Session。"""
    from agent.session import Session

    if args.session:
        try:
            session = session_mgr.load_session(args.session)
            print(f"[系统] 已恢复 Session：{args.session}")
            return session
        except FileNotFoundError:
            print(f"[错误] Session 不存在：{args.session}", file=sys.stderr)
            sys.exit(1)

    if args.resume:
        session = session_mgr.get_latest_session()
        if session:
            print(f"[系统] 已恢复最近 Session：{session.id}")
            return session
        else:
            print("[系统] 无历史 Session，创建新对话。")

    return session_mgr.new_session(work_dir=str(config.resolved_work_dir()))


def run_single_message(message: str, config, session, session_mgr) -> None:
    """非交互模式：发送单条消息后退出。"""
    from agent.llm import LLMClient
    from agent.loop import AgentLoop, ToolCallEvent, ToolResultEvent
    from agent.skills import load_skills

    llm = LLMClient(config.llm)
    skill_registry = load_skills(config)
    loop = AgentLoop(config, session, llm, skill_registry)

    def on_tool_call(e: ToolCallEvent) -> None:
        import json
        args_str = json.dumps(e.arguments, ensure_ascii=False)
        print(f"[调用工具] {e.name}  {args_str}", file=sys.stderr)

    def on_tool_result(e: ToolResultEvent) -> None:
        result = e.result[:200] + "..." if len(e.result) > 200 else e.result
        print(f"[工具结果] {result}", file=sys.stderr)

    if config.llm.streaming:
        for token in loop.stream_run(message, on_tool_call=on_tool_call, on_tool_result=on_tool_result):
            sys.stdout.write(token)
            sys.stdout.flush()
        print()
    else:
        answer = loop.run(message, on_tool_call=on_tool_call, on_tool_result=on_tool_result)
        print(answer)

    if config.session.auto_save:
        session_mgr.save_session(session)


def main() -> None:
    args = parse_args()

    # 1. 加载配置
    from agent.config import load_config
    config_path = Path(args.config) if args.config else None
    config = load_config(config_path)

    # 命令行覆盖
    if args.no_stream:
        config.llm.streaming = False
    setup_agent_scope(config, args.agent, interactive=args.message is None)

    # 检查 API Key
    if not config.llm.api_key:
        print(
            "[错误] 未配置 API Key。\n"
            "  请在 config.yaml 中设置 llm.api_key，\n"
            "  或设置环境变量 AGENT_API_KEY。",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. 初始化 Session 管理器
    from agent.session import SessionManager
    session_mgr = SessionManager(config.resolved_session_dir())

    # 3. 解析 Session
    session = resolve_session(args, session_mgr, config)

    # 4. 单条消息模式
    if args.message:
        run_single_message(args.message, config, session, session_mgr)
        return

    # 5. 交互模式
    from agent.cli import CLI
    from agent.skills import load_skills
    skill_registry = load_skills(config)
    cli = CLI(config, session, session_mgr, skill_registry)
    cli.run()


if __name__ == "__main__":
    main()
