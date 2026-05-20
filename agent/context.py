"""
上下文管理：每次 LLM 请求前临时构建 messages。

Session 只持久化事实历史；system prompt、Skill 摘要和运行时状态由这里
按需生成，不写入 session 文件。
"""

from __future__ import annotations

from .config import AgentConfig
from .memory import MarkdownMemoryStore
from .session import Session
from .skills import SkillRegistry, format_skills_for_prompt
from .tools import ToolContext


class ContextManager:
    def __init__(
        self,
        config: AgentConfig,
        skill_registry: SkillRegistry,
        memory_store: MarkdownMemoryStore | None = None,
    ) -> None:
        self._config = config
        self._skill_registry = skill_registry
        self._memory_store = memory_store

    def build_messages(self, session: Session, tool_ctx: ToolContext) -> list[dict]:
        system = self._build_system_prompt(session, tool_ctx)
        history = [m for m in session.messages if m.get("role") != "system"]
        return [{"role": "system", "content": system}, *history]

    def _build_system_prompt(self, session: Session, tool_ctx: ToolContext) -> str:
        skills = self._skill_registry.list_for_prompt()[: self._config.skills.max_prompt_items]
        skills_prompt = format_skills_for_prompt(skills)
        parts = [
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
        ]

        if self._memory_store is not None:
            parts.append(
                "Memory rules:\n"
                "- <memory> is long-term context across sessions.\n"
                "- Prefer it for stable user preferences, durable facts, and project conventions.\n"
                "- Write new memories only via memory_write.\n"
                "- If the user explicitly asks to remember something, use source=explicit_user_request with reason; it is written directly to long-term memory.\n"
                "- Otherwise use source=model_observation or source=model_inferred_stable_fact; it is written to daily memory first and may be promoted later.\n"
                "- Do not store secrets, credentials, cookies, API keys, or other sensitive values."
            )
            memory_text = self._memory_store.read_long_term_text()
            if memory_text.strip() or self._config.memory.context_inject_when_empty:
                parts.append(f"<memory>\n{self._truncate_memory(memory_text)}\n</memory>")

        return "\n\n".join(parts)

    def _truncate_memory(self, text: str) -> str:
        max_chars = self._config.memory.context_max_inject_chars
        if len(text) <= max_chars:
            return text
        marker = "\n\n[Memory truncated for context]\n"
        if max_chars <= len(marker):
            return marker[:max_chars]
        return text[: max_chars - len(marker)].rstrip() + marker.rstrip()
