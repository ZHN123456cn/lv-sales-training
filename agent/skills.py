"""
Skill 发现与注册。

Skill 采用 AgentSkills 风格：每个 Skill 是一个目录，包含带 YAML
frontmatter 的 SKILL.md。上下文中只注入 name/description；详细说明由
skill_read 工具按需读取。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any

import yaml

from .config import AgentConfig


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    file_path: Path
    base_dir: Path
    source: str = "workspace"


@dataclass
class SkillRegistry:
    _skills: dict[str, Skill] = field(default_factory=dict)

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_for_prompt(self) -> list[Skill]:
        return [self._skills[name] for name in sorted(self._skills)]

    def all(self) -> list[Skill]:
        return self.list_for_prompt()


def load_skills(config: AgentConfig) -> SkillRegistry:
    registry = SkillRegistry()
    if not config.skills.enabled:
        return registry
    for root in config.resolved_skill_dirs():
        _load_root(root, registry, config)
    return registry


def format_skills_for_prompt(skills: list[Skill]) -> str:
    if not skills:
        return "<available_skills />"

    lines = ["<available_skills>"]
    for skill in skills:
        lines.extend([
            "  <skill>",
            f"    <name>{escape(skill.name)}</name>",
            f"    <description>{escape(skill.description)}</description>",
            "  </skill>",
        ])
    lines.append("</available_skills>")
    return "\n".join(lines)


def read_skill_markdown(name: str, registry: SkillRegistry) -> str:
    skill = registry.get(name)
    if skill is None:
        return f"[错误] 未知 Skill：{name}"
    try:
        text = skill.file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"[错误] 读取 Skill 失败：{name}：{e}"

    return (
        f"[Skill] {skill.name}\n"
        f"[BaseDir] {skill.base_dir}\n\n"
        "--- SKILL.md ---\n"
        f"{text}\n\n"
        "相对路径说明：该 Skill 中提到的 references/ 或 scripts/ "
        "路径都相对于 BaseDir 解析。"
    )


def _load_root(root: Path, registry: SkillRegistry, config: AgentConfig) -> None:
    if not root.exists() or not root.is_dir():
        return
    root_resolved = root.resolve()
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir() or entry.name.startswith(".") or entry.name == "node_modules":
            continue
        skill_file = entry / "SKILL.md"
        if not skill_file.exists():
            continue
        skill = _load_skill(root_resolved, skill_file, config)
        if skill is not None:
            registry.register(skill)


def _load_skill(root: Path, skill_file: Path, config: AgentConfig) -> Skill | None:
    try:
        resolved = skill_file.resolve()
        if os.path.commonpath([str(root), str(resolved)]) != str(root):
            return None
        if resolved.stat().st_size > config.skills.max_file_bytes:
            return None
        text = resolved.read_text(encoding="utf-8", errors="replace")
        frontmatter = _parse_frontmatter(text)
        name = str(frontmatter.get("name") or resolved.parent.name).strip()
        description = str(frontmatter.get("description") or "").strip()
        if not name or not description:
            return None
        if len(description) > config.skills.max_description_chars:
            description = description[:config.skills.max_description_chars]
        return Skill(
            name=name,
            description=description,
            file_path=resolved,
            base_dir=resolved.parent,
        )
    except Exception:
        return None


def _parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end_index = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_index = idx
            break
    if end_index is None:
        return {}
    raw = "\n".join(lines[1:end_index])
    data = yaml.safe_load(raw) or {}
    return data if isinstance(data, dict) else {}
