"""
Session 管理：持久化对话历史。

Session 文件存储在 project_class/.sessions/ 目录：
  .sessions/index.json          — Session 索引（id -> 元信息）
  .sessions/<session-id>.json   — 各 Session 的对话历史

Session ID 格式：YYYYMMDD-HHMMSS-<随机6位十六进制>
  示例：20240420-143022-a3f7b1
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SessionMeta:
    id: str
    title: str
    created_at: str
    updated_at: str
    work_dir: str = "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "work_dir": self.work_dir,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionMeta":
        return cls(
            id=d["id"],
            title=d.get("title", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            work_dir=d.get("work_dir", "."),
        )


@dataclass
class Session:
    id: str
    created_at: str
    updated_at: str
    title: str = ""
    work_dir: str = "."
    messages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "title": self.title,
            "work_dir": self.work_dir,
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            id=d["id"],
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            title=d.get("title", ""),
            work_dir=d.get("work_dir", "."),
            messages=d.get("messages", []),
        )

    def to_meta(self) -> SessionMeta:
        return SessionMeta(
            id=self.id,
            title=self.title,
            created_at=self.created_at,
            updated_at=self.updated_at,
            work_dir=self.work_dir,
        )

    def touch(self) -> None:
        """更新 updated_at 为当前时间。"""
        self.updated_at = _now_iso()

    def update_title_from_messages(self) -> None:
        """从第一条用户消息自动生成标题（最多 30 字）。"""
        if self.title:
            return
        for msg in self.messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    self.title = content[:30].replace("\n", " ")
                    break


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class SessionManager:
    def __init__(self, session_dir: Path) -> None:
        self._dir = session_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "index.json"

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def new_session(self, work_dir: str = ".") -> Session:
        """创建并返回新 Session（不自动保存，由调用方决定时机）。"""
        session_id = _generate_session_id()
        now = _now_iso()
        return Session(
            id=session_id,
            created_at=now,
            updated_at=now,
            work_dir=work_dir,
        )

    def load_session(self, session_id: str) -> Session:
        """加载已有 Session，找不到时抛出 FileNotFoundError。"""
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session 不存在：{session_id}")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return Session.from_dict(data)

    def save_session(self, session: Session) -> None:
        """持久化 Session（同时更新索引）。"""
        session.touch()
        session.update_title_from_messages()

        # 写 session 文件
        path = self._session_path(session.id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)

        # 更新索引
        self._update_index(session.to_meta())

    def list_sessions(self) -> list[SessionMeta]:
        """返回所有 Session 的元信息列表，按 updated_at 降序。"""
        index = self._load_index()
        metas = [SessionMeta.from_dict(d) for d in index.get("sessions", [])]
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas

    def delete_session(self, session_id: str) -> bool:
        """删除 Session 文件和索引条目。返回是否成功。"""
        path = self._session_path(session_id)
        deleted = False
        if path.exists():
            path.unlink()
            deleted = True
        self._remove_from_index(session_id)
        return deleted

    def get_latest_session(self) -> Session | None:
        """加载最近一次活跃的 Session；无记录时返回 None。"""
        metas = self.list_sessions()
        if not metas:
            return None
        try:
            return self.load_session(metas[0].id)
        except FileNotFoundError:
            return None

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    def _session_path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"

    def _load_index(self) -> dict[str, Any]:
        if not self._index_path.exists():
            return {"sessions": []}
        with open(self._index_path, encoding="utf-8") as f:
            return json.load(f)

    def _save_index(self, index: dict[str, Any]) -> None:
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

    def _update_index(self, meta: SessionMeta) -> None:
        index = self._load_index()
        sessions: list[dict] = index.get("sessions", [])
        # 查找并更新或追加
        for i, entry in enumerate(sessions):
            if entry.get("id") == meta.id:
                sessions[i] = meta.to_dict()
                break
        else:
            sessions.append(meta.to_dict())
        index["sessions"] = sessions
        self._save_index(index)

    def _remove_from_index(self, session_id: str) -> None:
        index = self._load_index()
        sessions = [s for s in index.get("sessions", []) if s.get("id") != session_id]
        index["sessions"] = sessions
        self._save_index(index)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _generate_session_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(3)   # 6 位十六进制
    return f"{timestamp}-{suffix}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
