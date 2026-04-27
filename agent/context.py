"""
上下文管理：每次 LLM 请求前临时构建 messages。

Session 只持久化事实历史；system prompt、Skill 摘要和运行时状态由这里
按需生成，不写入 session 文件。
"""

from __future__ import annotations

from .config import AgentConfig
from .session import Session
from .skills import SkillRegistry, format_skills_for_prompt
from .tools import ToolContext


class ContextManager:
    def __init__(self, config: AgentConfig, skill_registry: SkillRegistry) -> None:
        self._config = config
        self._skill_registry = skill_registry

    def build_messages(self, session: Session, tool_ctx: ToolContext) -> list[dict]:
        system = self._build_system_prompt(session, tool_ctx)
        history = [m for m in session.messages if m.get("role") != "system"]
        return [{"role": "system", "content": system}, *history]

    def _build_system_prompt(self, session: Session, tool_ctx: ToolContext) -> str:
        skills = self._skill_registry.list_for_prompt()[: self._config.skills.max_prompt_items]
        skills_prompt = format_skills_for_prompt(skills)
        return "\n\n".join([
            self._config.formatted_system_prompt(),
            f"Session ID: {session.id}",
            f"Current work directory: {tool_ctx.work_dir}",
            (
                "Skill rules:\n"
                "- <available_skills> is an index, not the full instructions.\n"
                "- When a user task matches a skill description, call skill_read with that skill name first.\n"
                "- After skill_read returns SKILL.md, follow its steps and constraints.\n"
                "- Relative references such as references/ or scripts/ are resolved from the Skill BaseDir."
            ),
            skills_prompt,
        ])
