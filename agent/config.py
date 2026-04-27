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
from dotenv import load_dotenv

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
class AgentConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    max_iterations: int = 20
    work_dir: str = "."
    system_prompt: str = (
        "你是一个智能助手，可以使用工具帮助用户完成任务。"
        "工作目录为 {work_dir}。"
        "在使用工具时，请逐步思考并说明你的操作意图。"
    )
    enabled_tools: list[str] = field(default_factory=lambda: [
        "skill_read",
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
    }
    for env_key, (obj, attr) in overrides.items():
        val = os.environ.get(env_key)
        if val is not None:
            setattr(obj, attr, val)


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

    return cfg
