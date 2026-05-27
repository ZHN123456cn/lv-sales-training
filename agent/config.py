"""
配置加载模块。

加载顺序（后者覆盖前者）：
  1. 内置默认值
  2. config.yaml
  3. .env 文件
  4. 系统环境变量
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs) -> bool:
        return False

# project_class/ 目录（本文件的上一级）
PROJECT_ROOT = Path(__file__).parent.parent.resolve()


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class LLMConfig:
    provider: str = "deepseek"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    temperature: float = 0.7
    timeout: int = 60
    max_retries: int = 3
    streaming: bool = True


@dataclass
class SessionConfig:
    dir: str = ".sessions"
    max_history: int = 100
    auto_save: bool = True


@dataclass
class SkillsConfig:
    enabled: bool = True
    dirs: list[str] = field(default_factory=lambda: ["skills"])
    max_file_bytes: int = 262_144
    max_prompt_items: int = 50
    max_description_chars: int = 240


@dataclass
class MemoryConfig:
    enabled: bool = True
    backend: str = "markdown"
    root_dir: str = ".agent_memory"
    long_term_file: str = "MEMORY.md"
    daily_dir: str = "daily"
    context_max_inject_chars: int = 16_384
    context_inject_when_empty: bool = True
    search_keyword_enabled: bool = True
    search_vector_enabled: bool = True
    search_hybrid_enabled: bool = True
    search_max_results: int = 5
    search_max_snippet_chars: int = 500
    embedding_provider: str = "none"
    embedding_base_url: str = "http://localhost:11434"
    embedding_model: str = "embeddinggemma"
    embedding_timeout: int = 30
    promotion_enabled: bool = True
    promotion_min_recall_count: int = 3
    write_max_text_chars: int = 2_048
    admission_explicit_user_intent: bool = True
    admission_model_inferred_stable_fact: bool = True
    sensitive_patterns: list[str] = field(default_factory=lambda: [
        r"(?i)api[_-]?key",
        r"\b[0-9]{11}\b",
        r"(?i)password",
        r"(?i)cookie",
    ])


@dataclass
class MCPServerConfig:
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout: int = 30
    connect_timeout: int | None = None
    read_timeout: int | None = None
    call_timeout: int | None = None
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5

    @property
    def effective_connect_timeout(self) -> int | float:
        return self.connect_timeout if self.connect_timeout is not None else self.timeout

    @property
    def effective_read_timeout(self) -> int | float:
        return self.read_timeout if self.read_timeout is not None else self.timeout

    @property
    def effective_call_timeout(self) -> int | float:
        return self.call_timeout if self.call_timeout is not None else self.timeout


@dataclass
class MCPConfig:
    enabled: bool = False
    servers: dict[str, MCPServerConfig] = field(default_factory=dict)


@dataclass
class AgentConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    max_iterations: int = 20
    work_dir: str = "."
    system_prompt: str = (
        "你是一个智能助手，可以使用工具帮助用户完成任务。"
        "工作目录为 {work_dir}。"
        "在使用工具时，请逐步思考并说明你的操作意图。"
    )
    enabled_tools: list[str] = field(default_factory=lambda: [
        "skill_read",
        "memory_read",
        "memory_search",
        "memory_write",
        "memory_reindex",
        "read_file",
        "write_file",
        "list_dir",
        "change_dir",
        "shell",
        "search_files",
    ])

    # 运行时属性（非配置，由 load_config 填充）
    config_path: Path = field(default_factory=lambda: PROJECT_ROOT / "config.yaml")
    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)

    def resolved_work_dir(self) -> Path:
        """返回绝对工作目录路径。"""
        p = Path(self.work_dir)
        if p.is_absolute():
            return p
        return (self.project_root / p).resolve()

    def resolved_session_dir(self) -> Path:
        """返回绝对 session 目录路径。"""
        p = Path(self.session.dir)
        if p.is_absolute():
            return p
        return (self.project_root / p).resolve()

    def resolved_skill_dirs(self) -> list[Path]:
        """返回绝对 Skill 根目录列表。"""
        result = []
        for raw in self.skills.dirs:
            p = Path(raw)
            result.append(p if p.is_absolute() else (self.project_root / p).resolve())
        return result

    def resolved_memory_dir(self) -> Path:
        p = Path(self.memory.root_dir)
        if p.is_absolute():
            return p
        return (self.project_root / p).resolve()

    def resolved_memory_file(self) -> Path:
        memory_file = Path(self.memory.long_term_file)
        if memory_file.name != self.memory.long_term_file or self.memory.long_term_file in ("", ".", ".."):
            raise ValueError("memory.long_term_file must be a plain filename")
        return self.resolved_memory_dir() / self.memory.long_term_file

    def resolved_mcp_cwd(self, server: MCPServerConfig) -> Path | None:
        if server.cwd is None:
            return None
        p = Path(server.cwd)
        if p.is_absolute():
            return p
        return (self.project_root / p).resolve()

    def formatted_system_prompt(self) -> str:
        return self.system_prompt.format(work_dir=str(self.resolved_work_dir()))


# ---------------------------------------------------------------------------
# 加载逻辑
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并两个 dict，override 覆盖 base。"""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def _apply_env_overrides(cfg: AgentConfig) -> None:
    """将环境变量覆盖到配置对象上。"""
    overrides: dict[str, tuple[Any, type]] = {
        "AGENT_API_KEY":   (cfg.llm, "api_key"),
        "AGENT_BASE_URL":  (cfg.llm, "base_url"),
        "AGENT_MODEL":     (cfg.llm, "model"),
        "AGENT_WORK_DIR":  (cfg, "work_dir"),
        "AGENT_MEMORY_DIR": (cfg.memory, "root_dir"),
    }
    for env_key, (obj, attr) in overrides.items():
        val = os.environ.get(env_key)
        if val is not None:
            setattr(obj, attr, val)
    if os.environ.get("AGENT_MEMORY_DISABLED") == "1":
        cfg.memory.enabled = False


def load_config(config_path: Path | None = None) -> AgentConfig:
    """
    加载并返回 AgentConfig。

    Args:
        config_path: config.yaml 的路径，默认为 project_class/config.yaml
    """
    if config_path is None:
        config_path = PROJECT_ROOT / "config.yaml"

    # 1. 加载 .env（不覆盖已有环境变量）
    dotenv_path = PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path, override=False)

    # 2. 读取 yaml
    yaml_data = _read_yaml(config_path)

    # 3. 构建配置对象
    cfg = AgentConfig()
    cfg.config_path = config_path
    cfg.project_root = PROJECT_ROOT

    llm_data: dict = yaml_data.get("llm", {})
    for key, val in llm_data.items():
        if hasattr(cfg.llm, key):
            setattr(cfg.llm, key, val)

    session_data: dict = yaml_data.get("session", {})
    for key, val in session_data.items():
        if hasattr(cfg.session, key):
            setattr(cfg.session, key, val)

    skills_data: dict = yaml_data.get("skills", {})
    for key, val in skills_data.items():
        if key == "dirs" and isinstance(val, list):
            cfg.skills.dirs = [str(v) for v in val]
        elif hasattr(cfg.skills, key):
            setattr(cfg.skills, key, val)

    memory_data: dict = yaml_data.get("memory", {})
    for key, val in memory_data.items():
        if key == "context" and isinstance(val, dict):
            if "max_inject_chars" in val:
                cfg.memory.context_max_inject_chars = int(val["max_inject_chars"])
            if "inject_when_empty" in val:
                cfg.memory.context_inject_when_empty = bool(val["inject_when_empty"])
        elif key == "search" and isinstance(val, dict):
            if "keyword_enabled" in val:
                cfg.memory.search_keyword_enabled = bool(val["keyword_enabled"])
            if "vector_enabled" in val:
                cfg.memory.search_vector_enabled = bool(val["vector_enabled"])
            if "hybrid_enabled" in val:
                cfg.memory.search_hybrid_enabled = bool(val["hybrid_enabled"])
            if "max_results" in val:
                cfg.memory.search_max_results = int(val["max_results"])
            if "max_snippet_chars" in val:
                cfg.memory.search_max_snippet_chars = int(val["max_snippet_chars"])
            embedding = val.get("embedding")
            if isinstance(embedding, dict):
                if "provider" in embedding:
                    cfg.memory.embedding_provider = str(embedding["provider"])
                if "base_url" in embedding:
                    cfg.memory.embedding_base_url = str(embedding["base_url"])
                if "model" in embedding:
                    cfg.memory.embedding_model = str(embedding["model"])
                if "timeout" in embedding:
                    cfg.memory.embedding_timeout = int(embedding["timeout"])
        elif key == "promotion" and isinstance(val, dict):
            if "enabled" in val:
                cfg.memory.promotion_enabled = bool(val["enabled"])
            if "min_recall_count" in val:
                cfg.memory.promotion_min_recall_count = int(val["min_recall_count"])
        elif key == "memory_file":
            # Backward compatibility for earlier configs.
            cfg.memory.long_term_file = str(val)
        elif key == "write" and isinstance(val, dict):
            if "max_text_chars" in val:
                cfg.memory.write_max_text_chars = int(val["max_text_chars"])
            admission = val.get("admission")
            if isinstance(admission, dict):
                if "explicit_user_intent" in admission:
                    cfg.memory.admission_explicit_user_intent = bool(admission["explicit_user_intent"])
                if "model_inferred_stable_fact" in admission:
                    cfg.memory.admission_model_inferred_stable_fact = bool(admission["model_inferred_stable_fact"])
            if "sensitive_patterns" in val and isinstance(val["sensitive_patterns"], list):
                cfg.memory.sensitive_patterns = [str(v) for v in val["sensitive_patterns"]]
        elif hasattr(cfg.memory, key):
            setattr(cfg.memory, key, val)

    mcp_data: dict = yaml_data.get("mcp", {})
    if isinstance(mcp_data, dict):
        if "enabled" in mcp_data:
            cfg.mcp.enabled = bool(mcp_data["enabled"])
        servers_data = mcp_data.get("servers", {})
        if isinstance(servers_data, dict):
            for name, raw_server in servers_data.items():
                if not isinstance(raw_server, dict):
                    continue
                transport = str(raw_server.get("transport", "") or "").strip().lower()
                if not transport:
                    transport = "stdio" if raw_server.get("command") is not None else ""
                server = MCPServerConfig(
                    transport=transport,
                    command=str(raw_server.get("command", "")),
                    args=[str(v) for v in raw_server.get("args", [])],
                    env={str(k): str(v) for k, v in raw_server.get("env", {}).items()},
                    cwd=str(raw_server["cwd"]) if raw_server.get("cwd") is not None else None,
                    url=str(raw_server.get("url", "")),
                    headers={str(k): str(v) for k, v in raw_server.get("headers", {}).items()},
                    timeout=int(raw_server.get("timeout", 30)),
                    connect_timeout=(
                        int(raw_server["connect_timeout"])
                        if raw_server.get("connect_timeout") is not None
                        else None
                    ),
                    read_timeout=(
                        int(raw_server["read_timeout"])
                        if raw_server.get("read_timeout") is not None
                        else None
                    ),
                    call_timeout=(
                        int(raw_server["call_timeout"])
                        if raw_server.get("call_timeout") is not None
                        else None
                    ),
                    max_retries=int(raw_server.get("max_retries", 2)),
                    retry_backoff_seconds=float(raw_server.get("retry_backoff_seconds", 0.5)),
                )
                cfg.mcp.servers[str(name)] = server

    agent_data: dict = yaml_data.get("agent", {})
    for key, val in agent_data.items():
        if key == "work_dir":
            cfg.work_dir = val
        elif key == "max_iterations":
            cfg.max_iterations = int(val)
        elif key == "system_prompt":
            cfg.system_prompt = val

    tools_data: dict = yaml_data.get("tools", {})
    if "enabled" in tools_data and isinstance(tools_data["enabled"], list):
        cfg.enabled_tools = tools_data["enabled"]

    # 4. 环境变量覆盖
    _apply_env_overrides(cfg)
    cfg.resolved_memory_file()

    return cfg
