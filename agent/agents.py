"""Agent workspace discovery and scope helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_AGENT_ID = "main"
WORKSPACE_DIR = "workspace"
WORKSPACE_PREFIX = "workspace_"
IDENTITY_FILE = "IDENTITY.md"
MEMORY_FILE = "MEMORY.md"
DAILY_MEMORY_DIR = "memory"
SESSION_DIR = "sessions"
SKILLS_DIR = "skills"
DEFAULT_IDENTITY = (
    "# Identity\n\n"
    "你是一个智能助手，可以使用工具帮助用户完成任务。\n"
)
DEFAULT_MEMORY = "# Agent Memory\n"
_AGENT_ID_RE = re.compile(r"^[a-z0-9_-]+$")


@dataclass(frozen=True)
class AgentInfo:
    id: str
    workspace_dir: Path


@dataclass(frozen=True)
class AgentScope:
    id: str
    workspace_dir: Path
    identity_path: Path
    memory_path: Path
    daily_memory_dir: Path
    session_dir: Path
    skills_dir: Path


def normalize_agent_id(agent_id: str | None) -> str:
    if agent_id is None:
        normalized = DEFAULT_AGENT_ID
    else:
        normalized = str(agent_id).strip().lower()
    if not normalized:
        raise ValueError("agent name cannot be empty")
    if normalized in {".", ".."}:
        raise ValueError(f"invalid agent name: {agent_id}")
    if any(sep in normalized for sep in ("/", "\\")) or ":" in normalized:
        raise ValueError(f"invalid agent name: {agent_id}")
    if not _AGENT_ID_RE.fullmatch(normalized):
        raise ValueError(
            "agent name may only contain lowercase letters, digits, '-' and '_'"
        )
    return normalized


def workspace_dir_for(project_root: Path, agent_id: str) -> Path:
    agent_id = normalize_agent_id(agent_id)
    if agent_id == DEFAULT_AGENT_ID:
        return (project_root / WORKSPACE_DIR).resolve()
    return (project_root / f"{WORKSPACE_PREFIX}{agent_id}").resolve()


def scope_for(project_root: Path, agent_id: str) -> AgentScope:
    workspace_dir = workspace_dir_for(project_root, agent_id)
    return AgentScope(
        id=normalize_agent_id(agent_id),
        workspace_dir=workspace_dir,
        identity_path=workspace_dir / IDENTITY_FILE,
        memory_path=workspace_dir / MEMORY_FILE,
        daily_memory_dir=workspace_dir / DAILY_MEMORY_DIR,
        session_dir=workspace_dir / SESSION_DIR,
        skills_dir=workspace_dir / SKILLS_DIR,
    )


def workspace_exists(project_root: Path, agent_id: str) -> bool:
    return workspace_dir_for(project_root, agent_id).is_dir()


def ensure_agent_workspace(scope: AgentScope) -> None:
    scope.workspace_dir.mkdir(parents=True, exist_ok=True)
    ensure_agent_workspace_contents(scope)


def ensure_agent_workspace_contents(scope: AgentScope) -> None:
    if not scope.workspace_dir.exists():
        raise FileNotFoundError(f"agent workspace does not exist: {scope.workspace_dir}")
    if not scope.workspace_dir.is_dir():
        raise NotADirectoryError(str(scope.workspace_dir))
    if not scope.identity_path.exists():
        scope.identity_path.write_text(DEFAULT_IDENTITY, encoding="utf-8")
    if not scope.memory_path.exists():
        scope.memory_path.write_text(DEFAULT_MEMORY, encoding="utf-8")
    scope.daily_memory_dir.mkdir(parents=True, exist_ok=True)
    scope.session_dir.mkdir(parents=True, exist_ok=True)
    scope.skills_dir.mkdir(parents=True, exist_ok=True)


def list_agents(project_root: Path) -> list[AgentInfo]:
    result: list[AgentInfo] = []
    main_dir = project_root / WORKSPACE_DIR
    if main_dir.is_dir():
        result.append(AgentInfo(DEFAULT_AGENT_ID, main_dir.resolve()))
    for path in sorted(project_root.glob(f"{WORKSPACE_PREFIX}*"), key=lambda p: p.name.lower()):
        if not path.is_dir():
            continue
        raw_id = path.name[len(WORKSPACE_PREFIX):]
        try:
            agent_id = normalize_agent_id(raw_id)
        except ValueError:
            continue
        result.append(AgentInfo(agent_id, path.resolve()))
    return result


def read_identity(scope: AgentScope) -> str:
    ensure_agent_workspace_contents(scope)
    return scope.identity_path.read_text(encoding="utf-8", errors="replace")
