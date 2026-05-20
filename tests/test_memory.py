from __future__ import annotations

import json
import uuid

from agent.context import ContextManager
from agent.config import MemoryConfig
from agent.memory import MarkdownMemoryStore
from agent.tools import ToolContext, execute_tool


def make_store(tmp_path):
    return MarkdownMemoryStore(tmp_path / ".agent_memory" / "MEMORY.md")


def test_ensure_file_creates_skeleton(tmp_path):
    store = make_store(tmp_path)

    text = store.read_text()

    assert "# Agent Memory" in text
    assert "## user" not in text
    assert "## project" not in text
    assert "## session" not in text


def test_add_text_appends_to_long_term_root(tmp_path):
    store = make_store(tmp_path)

    result = store.add_text(
        text="User prefers Python.",
        source="explicit_user_request",
        reason="User asked the agent to remember this.",
    )

    assert result.blocked is False
    uuid.UUID(result.id)
    text = store.read_text()
    assert "User prefers Python." in text
    assert "scope:" not in text


def test_add_text_blocks_sensitive_pattern(tmp_path):
    store = make_store(tmp_path)

    result = store.add_text(
        text="api_key is secret",
        source="explicit_user_request",
        reason="test",
    )

    assert result.blocked is True
    assert result.reason == "sensitive_pattern"
    assert "api_key is secret" not in store.read_text()


def test_add_text_requires_reason(tmp_path):
    store = make_store(tmp_path)

    result = store.add_text(
        text="Use pytest.",
        source="explicit_user_request",
        reason="",
    )

    assert result.blocked is True
    assert result.reason == "missing_reason"


def test_search_long_term_without_scope_filter(tmp_path):
    store = make_store(tmp_path)
    store.add_text("User likes Python.", "explicit_user_request", "explicit")
    store.add_text("Project uses pytest.", "explicit_user_request", "explicit")

    results = store.search("Python")

    assert len(results) == 1
    assert "Python" in results[0].snippet


def test_fts5_chinese_search_uses_jieba_tokens(tmp_path):
    store = make_store(tmp_path)
    store.add_long_term("项目使用中文分词提升关键词检索。", "explicit_user_request", "explicit")

    results = store.search("中文分词", tier="long_term")

    assert results
    assert "中文分词" in results[0].snippet


def test_fts5_quoted_chinese_query_does_not_break_match_syntax(tmp_path):
    store = make_store(tmp_path)
    store.add_long_term("用户喜欢科幻小说。", "explicit_user_request", "explicit")

    results = store.search('"科幻"', tier="long_term")

    assert results
    assert "科幻小说" in results[0].snippet


def test_memory_tools_read_search_write(tmp_path):
    store = make_store(tmp_path)
    ctx = ToolContext(work_dir=tmp_path, session_id="s1", memory_store=store)

    raw = execute_tool("memory_write", {
        "tier": "long_term",
        "text": "Project keeps memory in Markdown.",
        "source": "model_observation",
    }, ctx)
    write_result = json.loads(raw)
    assert write_result["blocked"] is False
    assert write_result["id"]

    read_result = execute_tool("memory_read", {}, ctx)
    assert "Project keeps memory in Markdown." not in read_result
    assert "Project keeps memory in Markdown." in store.read_daily_text()

    search_result = json.loads(execute_tool("memory_search", {"query": "Markdown"}, ctx))
    assert search_result[0]["tier"] == "daily"
    assert "scope" not in search_result[0]


def test_add_daily_and_read_back(tmp_path):
    store = make_store(tmp_path)

    result = store.add_daily("Investigated flaky test.", "model_observation", day="2026-05-18")

    assert result.blocked is False
    uuid.UUID(result.id)
    text = store.read_daily_text("2026-05-18")
    assert "# Daily Memory 2026-05-18" in text
    assert "Investigated flaky test." in text


def test_write_policy_routes_explicit_request_to_long_term(tmp_path):
    store = make_store(tmp_path)

    result = store.write_with_policy(
        text="User prefers concise answers.",
        source="explicit_user_request",
        reason="User explicitly asked the agent to remember this.",
    )

    assert result.blocked is False
    assert "User prefers concise answers." in store.read_long_term_text()


def test_write_policy_routes_non_explicit_memory_to_daily(tmp_path):
    store = make_store(tmp_path)

    result = store.write_with_policy(
        text="Observed that the project uses pytest.",
        source="model_inferred_stable_fact",
    )

    assert result.blocked is False
    assert "Observed that the project uses pytest." not in store.read_long_term_text()
    assert "Observed that the project uses pytest." in store.read_daily_text()


def test_search_daily_increments_recall_count(tmp_path):
    store = make_store(tmp_path)
    item_id = store.add_daily("Daily clue about sqlite indexing.", "model_observation", day="2026-05-18").id

    results = store.search("sqlite", tier="daily")

    assert results
    text = store.read_daily_text("2026-05-18")
    assert f"- id: {item_id}" in text
    assert "recall_count: 1" in text


def test_promote_daily_copies_to_long_term(tmp_path):
    store = make_store(tmp_path)
    store.config.promotion_min_recall_count = 1
    item_id = store.add_daily("Prefer deterministic migrations.", "model_observation", day="2026-05-18").id
    store.search("deterministic", tier="daily")

    promoted_id = store.promote_daily(item_id, "Repeatedly useful project convention.")

    assert promoted_id
    assert "Prefer deterministic migrations." in store.read_long_term_text()
    assert f"promoted_to: {promoted_id}" in store.read_daily_text("2026-05-18")


def test_memory_reindex_rebuilds_after_manual_edit(tmp_path):
    store = make_store(tmp_path)
    store.long_term_path.write_text(
        "# Agent Memory\n\n"
        "- id: manual-1\n"
        "  created_at: 2026-05-18T00:00:00+00:00\n"
        "  source: explicit_user_request\n"
        "  reason: manual edit\n"
        "  text: Manual memory survives reindex.\n",
        encoding="utf-8",
    )

    store.rebuild_index()
    results = store.search("Manual", tier="long_term")

    assert results
    assert results[0].item.id == "manual-1"


def test_index_status_exposes_search_backend_state(tmp_path):
    store = make_store(tmp_path)

    status = store.index_status()

    assert "fts5_enabled" in status
    assert "sqlite_vec_enabled" in status
    assert "embedding_provider" in status
    assert "embedding_model" in status
    assert "embedding_dimensions" in status
    assert status["keyword_tokenizer"] in {"jieba", "regex_fallback"}


def test_ollama_embedding_is_persisted_in_vector_store(tmp_path, monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"embeddings": [[0.1, 0.2, 0.3]]}'

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: FakeResponse())
    cfg = MemoryConfig(embedding_provider="ollama")
    store = MarkdownMemoryStore(tmp_path / ".agent_memory" / "MEMORY.md", cfg)

    item_id = store.add_long_term("Use real embeddings.", "explicit_user_request", "remember").id

    with store._connect() as conn:
        row = conn.execute("SELECT embedding FROM memory_vectors WHERE item_id = ?", (item_id,)).fetchone()
    assert row is not None
    assert json.loads(row["embedding"]) == [0.1, 0.2, 0.3]


def test_embedding_model_change_requires_reindex(tmp_path, monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"embeddings": [[0.1, 0.2, 0.3]]}'

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: FakeResponse())
    path = tmp_path / ".agent_memory" / "MEMORY.md"
    first = MarkdownMemoryStore(path, MemoryConfig(embedding_provider="ollama", embedding_model="m1"))
    first.add_long_term("Use one model.", "explicit_user_request", "remember")

    second = MarkdownMemoryStore(path, MemoryConfig(embedding_provider="ollama", embedding_model="m2"))

    assert second.index_status()["embedding_last_error"] == "embedding_config_changed_reindex_required"


def test_context_includes_memory_block(default_config, empty_session, tool_ctx, skill_registry, tmp_path):
    store = make_store(tmp_path)
    store.add_text("Memory is injected.", "explicit_user_request", "test")
    manager = ContextManager(default_config, skill_registry, store)

    messages = manager.build_messages(empty_session, tool_ctx)

    system = messages[0]["content"]
    assert "Memory rules:" in system
    assert "<memory>" in system
    assert "Memory is injected." in system
