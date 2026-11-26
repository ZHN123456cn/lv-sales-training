"""
Agent Manager：后端 Agent 生命周期管理（单例）。

职责：
  - 加载 AgentConfig（复用现有 agent.config.load_config）
  - 非交互式初始化 AgentScope（绕过 CLI 的 input() 提示）
  - 维护共享的 LLMClient 和 SkillRegistry（只读，可跨请求复用）
  - 为每个 Session 缓存一个 AgentLoop（AgentLoop 持有 session 状态，且
    初始化可能启动 MCP 子进程，代价较高，不宜每请求重建）
  - 线程安全：使用锁保护 loop 字典，避免并发请求创建重复实例

使用方式：
  manager = AgentManager.get_instance()
  loop = manager.get_or_create_loop(session)
  for token in loop.stream_run(message):
      ...
"""

from __future__ import annotations

import threading
from pathlib import Path

from agent.agents import (
    ensure_agent_workspace,
    ensure_agent_workspace_contents,
    normalize_agent_id,
    read_identity,
    scope_for,
    workspace_exists,
)
from agent.config import AgentConfig, load_config
from agent.llm import LLMClient
from agent.loop import AgentLoop
from agent.session import Session, SessionManager
from agent.skills import SkillRegistry, load_skills


class AgentManager:
    """单例 Agent 管理器。"""

    _instance: "AgentManager | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._config: AgentConfig = load_config()
        self._init_agent_scope(normalize_agent_id(self._config.agents.default))
        # LLMClient 延迟初始化：没有 API Key 时也能启动服务，
        # 仅在首次实际调用聊天时才创建（避免启动即崩溃）。
        self._llm: LLMClient | None = None
        self._llm_lock = threading.Lock()
        self._skill_registry: SkillRegistry = load_skills(self._config)
        # session_id -> AgentLoop
        self._loops: dict[str, AgentLoop] = {}
        self._loops_lock = threading.Lock()
        # 正在处理中的 session（防止同一 session 并发请求导致消息错乱）
        self._busy_sessions: set[str] = set()
        self._busy_lock = threading.Lock()
        # 固定的 SessionManager（使用项目根目录下的 .sessions，不随 agent 切换）
        # 这样培训 session 在 customer/coach agent 切换后仍能正确加载
        self._session_manager = SessionManager(self._config.project_root / ".sessions")

    # ------------------------------------------------------------------
    # 单例
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "AgentManager":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """关闭所有 loop 并重置单例（测试用）。"""
        if cls._instance is not None:
            cls._instance.close_all_loops()
        with cls._instance_lock:
            cls._instance = None

    # ------------------------------------------------------------------
    # AgentScope 初始化（非交互式，替代 main.py 的 setup_agent_scope）
    # ------------------------------------------------------------------

    def _init_agent_scope(self, agent_id: str) -> None:
        """确保指定 Agent workspace 存在，并绑定到 config。"""
        scope = scope_for(self._config.project_root, agent_id)
        if not workspace_exists(self._config.project_root, agent_id):
            ensure_agent_workspace(scope)
        else:
            ensure_agent_workspace_contents(scope)
        self._config.agent_scope = scope
        self._config.identity_text = read_identity(scope)

    def switch_agent(self, agent_id: str) -> None:
        """切换当前 Agent（非交互式）。会清空所有缓存的 loop。"""
        agent_id = normalize_agent_id(agent_id)
        self.close_all_loops()
        self._init_agent_scope(agent_id)
        self._skill_registry = load_skills(self._config)

    # ------------------------------------------------------------------
    # 属性访问
    # ------------------------------------------------------------------

    @property
    def config(self) -> AgentConfig:
        return self._config

    @property
    def llm(self) -> LLMClient:
        """延迟初始化 LLMClient。没有 API Key 时抛出明确错误。"""
        if self._llm is None:
            with self._llm_lock:
                if self._llm is None:
                    if not self._config.llm.api_key:
                        raise RuntimeError(
                            "未配置 LLM API Key。请在 config.yaml 中设置 llm.api_key，"
                            "或在 .env 中设置 AGENT_API_KEY 环境变量。"
                        )
                    self._llm = LLMClient(self._config.llm)
        return self._llm

    @property
    def skill_registry(self) -> SkillRegistry:
        return self._skill_registry

    @property
    def current_agent_id(self) -> str:
        return self._config.agent_scope.id if self._config.agent_scope else "main"

    @property
    def session_manager(self) -> SessionManager:
        """固定的 SessionManager，使用项目根目录下的 .sessions，不随 agent 切换。"""
        return self._session_manager

    def register_scenario_prompt(self, scenario_id: str, text: str) -> None:
        """登记场景正文，供 ContextManager 每轮拼 `<scenario>` 段。

        存在 `config.scenario_prompts` 这个**映射**里而不是 config 的单值字段：
        整个 manager 共享同一个 AgentConfig 实例，写单值会让后创建的会话
        覆盖先创建的会话（顾客 A 用着顾客 B 的场景，且不报错）。
        映射只读、按 id 取值，共享是安全的。
        """
        if not scenario_id:
            return
        self._config.scenario_prompts[str(scenario_id)] = str(text)

    # ------------------------------------------------------------------
    # AgentLoop 缓存
    # ------------------------------------------------------------------

    def get_or_create_loop(self, session: Session) -> AgentLoop:
        """
        获取或创建与 session 关联的 AgentLoop。
        同一 session_id 复用同一 loop；不同 session 各自独立。
        """
        with self._loops_lock:
            loop = self._loops.get(session.id)
            if loop is None:
                loop = AgentLoop(
                    config=self._config,
                    session=session,
                    llm=self.llm,
                    skill_registry=self._skill_registry,
                )
                self._loops[session.id] = loop
            return loop

    def invalidate_loop(self, session_id: str) -> None:
        """关闭并移除指定 session 的 loop（session 删除/切换时调用）。"""
        with self._loops_lock:
            loop = self._loops.pop(session_id, None)
        if loop is not None:
            loop.close()

    def close_all_loops(self) -> None:
        """关闭所有缓存的 loop。"""
        with self._loops_lock:
            loops = list(self._loops.values())
            self._loops.clear()
        for loop in loops:
            loop.close()

    # ------------------------------------------------------------------
    # Session 并发保护
    # ------------------------------------------------------------------

    def try_acquire_session(self, session_id: str) -> bool:
        """
        尝试占用一个 session，防止并发请求。
        返回 True 表示成功占用，False 表示该 session 正在处理其他请求。
        """
        with self._busy_lock:
            if session_id in self._busy_sessions:
                return False
            self._busy_sessions.add(session_id)
            return True

    def release_session(self, session_id: str) -> None:
        """释放 session 占用。"""
        with self._busy_lock:
            self._busy_sessions.discard(session_id)
