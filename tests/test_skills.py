from __future__ import annotations

from pathlib import Path

from agent.config import AgentConfig
from agent.skills import Skill, SkillRegistry, format_skills_for_prompt, load_skills


def write_skill(root: Path, dirname: str, frontmatter: str, body: str = "Body") -> Path:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")
    return skill_file


def test_load_skill_from_directory(tmp_path: Path):
    root = tmp_path / "skills"
    write_skill(root, "research", "name: research\ndescription: Research tasks")
    cfg = AgentConfig()
    cfg.project_root = tmp_path
    cfg.skills.dirs = [str(root)]

    registry = load_skills(cfg)
    skill = registry.get("research")

    assert skill is not None
    assert skill.description == "Research tasks"
    assert skill.file_path.name == "SKILL.md"


def test_skill_name_defaults_to_directory(tmp_path: Path):
    root = tmp_path / "skills"
    write_skill(root, "research", "description: Research tasks")
    cfg = AgentConfig()
    cfg.project_root = tmp_path
    cfg.skills.dirs = [str(root)]

    registry = load_skills(cfg)

    assert registry.get("research") is not None


def test_missing_description_is_skipped(tmp_path: Path):
    root = tmp_path / "skills"
    write_skill(root, "bad", "name: bad")
    cfg = AgentConfig()
    cfg.project_root = tmp_path
    cfg.skills.dirs = [str(root)]

    registry = load_skills(cfg)

    assert registry.get("bad") is None


def test_duplicate_skill_last_root_wins(tmp_path: Path):
    low = tmp_path / "low"
    high = tmp_path / "high"
    write_skill(low, "demo", "name: demo\ndescription: Low")
    write_skill(high, "demo", "name: demo\ndescription: High")
    cfg = AgentConfig()
    cfg.project_root = tmp_path
    cfg.skills.dirs = [str(low), str(high)]

    registry = load_skills(cfg)

    assert registry.get("demo").description == "High"


def test_format_skills_for_prompt_uses_name_and_description_only(tmp_path: Path):
    registry = SkillRegistry()
    registry.register(Skill(
        name="b",
        description="Bee",
        file_path=tmp_path / "b" / "SKILL.md",
        base_dir=tmp_path / "b",
    ))
    registry.register(Skill(
        name="a",
        description="Aye",
        file_path=tmp_path / "a" / "SKILL.md",
        base_dir=tmp_path / "a",
    ))

    prompt = format_skills_for_prompt(registry.list_for_prompt())

    assert "<available_skills>" in prompt
    assert "<name>a</name>" in prompt
    assert "<description>Aye</description>" in prompt
    assert "SKILL.md" not in prompt
    assert prompt.index("<name>a</name>") < prompt.index("<name>b</name>")


def test_empty_skills_prompt():
    assert format_skills_for_prompt([]) == "<available_skills />"


def test_skill_file_size_limit(tmp_path: Path):
    root = tmp_path / "skills"
    write_skill(root, "large", "name: large\ndescription: Large", "x" * 100)
    cfg = AgentConfig()
    cfg.project_root = tmp_path
    cfg.skills.dirs = [str(root)]
    cfg.skills.max_file_bytes = 10

    registry = load_skills(cfg)

    assert registry.get("large") is None
