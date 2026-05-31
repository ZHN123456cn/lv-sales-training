from __future__ import annotations

import pytest

from agent.agents import (
    ensure_agent_workspace,
    ensure_agent_workspace_contents,
    list_agents,
    normalize_agent_id,
    scope_for,
    workspace_dir_for,
)


def test_main_workspace_dir(tmp_path):
    assert workspace_dir_for(tmp_path, "main") == (tmp_path / "workspace").resolve()


def test_named_workspace_dir(tmp_path):
    assert workspace_dir_for(tmp_path, "work") == (tmp_path / "workspace_work").resolve()


@pytest.mark.parametrize("name", ["", ".", "..", "../x", "x/y", "C:\\x", "中文", "a b"])
def test_invalid_agent_names(name):
    with pytest.raises(ValueError):
        normalize_agent_id(name)


def test_create_agent_workspace_creates_structure(tmp_path):
    scope = scope_for(tmp_path, "work")
    ensure_agent_workspace(scope)

    assert scope.workspace_dir.is_dir()
    assert scope.identity_path.is_file()
    assert scope.memory_path.is_file()
    assert scope.daily_memory_dir.is_dir()
    assert scope.session_dir.is_dir()
    assert scope.skills_dir.is_dir()


def test_existing_workspace_is_completed(tmp_path):
    scope = scope_for(tmp_path, "work")
    scope.workspace_dir.mkdir()

    ensure_agent_workspace_contents(scope)

    assert scope.identity_path.is_file()
    assert scope.memory_path.is_file()
    assert scope.daily_memory_dir.is_dir()
    assert scope.session_dir.is_dir()
    assert scope.skills_dir.is_dir()


def test_list_agents(tmp_path):
    ensure_agent_workspace(scope_for(tmp_path, "main"))
    ensure_agent_workspace(scope_for(tmp_path, "work"))

    agents = list_agents(tmp_path)

    assert [agent.id for agent in agents] == ["main", "work"]
