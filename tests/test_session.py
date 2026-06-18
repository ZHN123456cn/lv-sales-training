"""
test_session.py — Session 管理测试

覆盖 agent/session.py：
- Session ID 生成
- 保存 / 加载 / 列举 / 删除
- 索引维护
- 标题自动生成
- touch() 更新时间
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from agent.session import Session, SessionManager, _generate_session_id, _now_iso


# ---------------------------------------------------------------------------
# Session ID 生成
# ---------------------------------------------------------------------------

class TestSessionIdGeneration:
    def test_unique_ids(self):
        """连续生成两个 ID，结果不相同。"""
        id1 = _generate_session_id()
        time.sleep(0.01)
        id2 = _generate_session_id()
        assert id1 != id2

    def test_id_format(self):
        """Session ID 符合 YYYYMMDD-HHMMSS-xxxxxx 格式。"""
        sid = _generate_session_id()
        pattern = r"^\d{8}-\d{6}-[0-9a-f]{6}$"
        assert re.match(pattern, sid), f"ID 格式不符：{sid}"

    def test_new_session_has_unique_ids(self, session_mgr: SessionManager):
        """通过 SessionManager 创建两个 Session，ID 不同。"""
        s1 = session_mgr.new_session()
        time.sleep(0.01)
        s2 = session_mgr.new_session()
        assert s1.id != s2.id


# ---------------------------------------------------------------------------
# 保存 & 加载
# ---------------------------------------------------------------------------

class TestSaveAndLoad:
    def test_roundtrip(self, session_mgr: SessionManager):
        """保存后加载，messages 和元信息完整还原。"""
        s = session_mgr.new_session()
        s.messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        session_mgr.save_session(s)
        loaded = session_mgr.load_session(s.id)
        assert loaded.id == s.id
        assert len(loaded.messages) == 2
        assert loaded.messages[0]["content"] == "你好"

    def test_save_creates_session_file(self, session_mgr: SessionManager, tmp_path: Path):
        """保存后 session 文件确实存在。"""
        s = session_mgr.new_session()
        session_mgr.save_session(s)
        session_file = tmp_path / ".sessions" / f"{s.id}.json"
        assert session_file.exists()

    def test_save_creates_index(self, session_mgr: SessionManager, tmp_path: Path):
        """首次保存后 index.json 被创建。"""
        s = session_mgr.new_session()
        session_mgr.save_session(s)
        index_file = tmp_path / ".sessions" / "index.json"
        assert index_file.exists()

    def test_save_updates_existing_index_entry(self, session_mgr: SessionManager, tmp_path: Path):
        """同一 Session 多次保存，index 中只有一条记录。"""
        s = session_mgr.new_session()
        session_mgr.save_session(s)
        s.messages.append({"role": "user", "content": "第二条"})
        session_mgr.save_session(s)
        index = json.loads((tmp_path / ".sessions" / "index.json").read_text(encoding="utf-8"))
        ids = [e["id"] for e in index["sessions"]]
        assert ids.count(s.id) == 1

    def test_load_nonexistent_raises(self, session_mgr: SessionManager):
        """加载不存在的 ID 抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            session_mgr.load_session("nonexistent-id-000000")

    def test_work_dir_preserved(self, session_mgr: SessionManager):
        """work_dir 字段在保存/加载后保持一致。"""
        s = session_mgr.new_session(work_dir="/custom/path")
        session_mgr.save_session(s)
        loaded = session_mgr.load_session(s.id)
        assert loaded.work_dir == "/custom/path"


# ---------------------------------------------------------------------------
# 列举
# ---------------------------------------------------------------------------

class TestListSessions:
    def test_list_sorted_by_updated_at(self, session_mgr: SessionManager):
        """list_sessions 按 updated_at 降序排列（最新在前）。"""
        s1 = session_mgr.new_session()
        s1.messages.append({"role": "user", "content": "first"})
        session_mgr.save_session(s1)

        time.sleep(1.1)  # 确保时间戳不同（精度到秒）

        s2 = session_mgr.new_session()
        s2.messages.append({"role": "user", "content": "second"})
        session_mgr.save_session(s2)

        metas = session_mgr.list_sessions()
        assert metas[0].id == s2.id   # 最新的排在前面
        assert metas[1].id == s1.id

    def test_list_empty(self, session_mgr: SessionManager):
        """无任何记录时返回空列表。"""
        assert session_mgr.list_sessions() == []


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------

class TestDeleteSession:
    def test_delete_removes_file(self, session_mgr: SessionManager, tmp_path: Path):
        """删除后 session 文件消失。"""
        s = session_mgr.new_session()
        session_mgr.save_session(s)
        session_mgr.delete_session(s.id)
        assert not (tmp_path / ".sessions" / f"{s.id}.json").exists()

    def test_delete_removes_from_index(self, session_mgr: SessionManager, tmp_path: Path):
        """删除后 index.json 中无对应记录。"""
        s = session_mgr.new_session()
        session_mgr.save_session(s)
        session_mgr.delete_session(s.id)
        index = json.loads((tmp_path / ".sessions" / "index.json").read_text(encoding="utf-8"))
        ids = [e["id"] for e in index["sessions"]]
        assert s.id not in ids

    def test_delete_returns_true_on_success(self, session_mgr: SessionManager):
        """删除已存在的 Session 返回 True。"""
        s = session_mgr.new_session()
        session_mgr.save_session(s)
        assert session_mgr.delete_session(s.id) is True

    def test_delete_nonexistent_returns_false(self, session_mgr: SessionManager):
        """删除不存在的 Session 返回 False，不报错。"""
        assert session_mgr.delete_session("nonexistent-id-111111") is False


# ---------------------------------------------------------------------------
# get_latest_session
# ---------------------------------------------------------------------------

class TestGetLatestSession:
    def test_returns_most_recent(self, session_mgr: SessionManager):
        """返回最近更新的 Session。"""
        s1 = session_mgr.new_session()
        s1.messages.append({"role": "user", "content": "older"})
        session_mgr.save_session(s1)

        time.sleep(1.1)

        s2 = session_mgr.new_session()
        s2.messages.append({"role": "user", "content": "newer"})
        session_mgr.save_session(s2)

        latest = session_mgr.get_latest_session()
        assert latest is not None
        assert latest.id == s2.id

    def test_returns_none_when_empty(self, session_mgr: SessionManager):
        """无任何记录时返回 None。"""
        assert session_mgr.get_latest_session() is None


# ---------------------------------------------------------------------------
# Session 辅助方法
# ---------------------------------------------------------------------------

class TestSessionHelpers:
    def test_auto_title_from_first_user_message(self, session_mgr: SessionManager):
        """首条 user 消息内容自动成为 title（最多 30 字）。"""
        s = session_mgr.new_session()
        s.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "这是一个很长的用户问题用来测试标题截断功能的文字"},
        ]
        session_mgr.save_session(s)
        loaded = session_mgr.load_session(s.id)
        assert len(loaded.title) <= 30
        assert "这是" in loaded.title

    def test_title_not_overwritten_if_set(self, session_mgr: SessionManager):
        """已有 title 时不覆盖。"""
        s = session_mgr.new_session()
        s.title = "手动标题"
        s.messages = [{"role": "user", "content": "用户问题"}]
        session_mgr.save_session(s)
        loaded = session_mgr.load_session(s.id)
        assert loaded.title == "手动标题"

    def test_touch_updates_updated_at(self):
        """touch() 后 updated_at 变为当前时间（与 created_at 不同）。"""
        s = Session(
            id="test-touch",
            created_at="2020-01-01T00:00:00Z",
            updated_at="2020-01-01T00:00:00Z",
        )
        s.touch()
        assert s.updated_at != "2020-01-01T00:00:00Z"
        assert s.updated_at.endswith("Z")
