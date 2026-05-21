"""分层 Markdown 记忆存储，并配套可重建的 SQLite 搜索索引。"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import AgentConfig, MemoryConfig

try:
    import jieba
    jieba.setLogLevel(logging.WARNING)
except ImportError:  # pragma: no cover - 已声明依赖；缺失时回退，保证启动不崩溃
    jieba = None


MemoryScope = str
MemoryTier = str
MemorySource = str

VALID_TIERS = {"long_term", "daily"}
VALID_SOURCES = {
    "explicit_user_request",
    "model_inferred_stable_fact",
    "model_observation",
    "promoted_daily_memory",
}
LONG_TERM_SOURCES = {
    "explicit_user_request",
    "promoted_daily_memory",
}
SKELETON = "# Agent Memory\n"


@dataclass(frozen=True)
class MemoryItem:
    """标准化后的单条记忆记录，不绑定具体持久化位置。

    长期记忆保存在主 Markdown 文件中，并归属于 user/project/session 等作用域。
    日记忆保存在按日期拆分的 Markdown 文件中，额外记录召回次数和晋升信息，便于
    将反复被检索到的观察结果晋升为长期记忆。SQLite 索引也使用同一份数据结构，
    这样搜索逻辑只需要处理 MemoryItem，而不需要关心记录来自 Markdown 还是索引。
    """

    id: str
    tier: MemoryTier
    text: str
    created_at: str
    source: str
    reason: str | None = None
    daily_date: str | None = None
    recall_count: int = 0
    promoted_from: str | None = None
    promoted_to: str | None = None


@dataclass(frozen=True)
class MemorySearchResult:
    """返回给工具层和上下文构建逻辑的搜索结果。

    这里同时保留完整 MemoryItem 和截断后的 snippet：调用方可以展示紧凑结果，
    同时仍然拿得到更新召回计数、晋升日记忆所需的完整元数据。
    """

    item: MemoryItem
    score: float
    snippet: str
    matched_by: str


@dataclass
class MemoryWriteResult:
    """记忆写入操作的结构化结果。

    写入会经过策略校验。被拒绝的写入不是异常情况：工具调用方需要机器可读的
    reason 来向上层解释为什么没有写入，而不是让 agent 主循环崩溃。
    """

    id: str | None = None
    blocked: bool = False
    reason: str | None = None

    def as_dict(self) -> dict:
        """返回 memory 工具对外暴露的紧凑 JSON 结构。"""
        data = {"blocked": self.blocked}
        if self.id is not None:
            data["id"] = self.id
        if self.reason is not None:
            data["reason"] = self.reason
        return data


@dataclass
class MarkdownMemoryStore:
    """以 Markdown 为真源、SQLite 为可重建索引的记忆存储。

    Markdown 文件是持久化真源，因为用户可以直接查看和手工编辑。SQLite 只是派生
    索引：可以删除，也可以随时从 Markdown 重建。这个拆分让持久化格式保持简单，
    同时仍然支持快速关键词搜索和可选的向量搜索。
    """

    long_term_path: Path
    config: MemoryConfig = field(default_factory=MemoryConfig)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        """初始化文件路径，并确保 SQLite 索引处于可用状态。

        启动时会保证三件事：
        1. 长期记忆 Markdown 文件存在；
        2. SQLite schema 已创建，并记录当前可用的搜索后端；
        3. 如果索引为空但 Markdown 中已有记忆，则自动重建索引。
        """
        self.root_dir = self.long_term_path.parent
        self.daily_dir = self.root_dir / self.config.daily_dir
        self.index_path = self.root_dir / "memory.sqlite3"
        self._fts_available = False
        self._vec_available = False
        self._vector_dimensions: int | None = None
        self._embedding_last_error: str | None = None
        self.ensure_file()
        self._ensure_index()
        if self._index_is_empty() and self._read_all_items():
            self.rebuild_index()

    @property
    def path(self) -> Path:
        """当前 CLI 仍在使用的向后兼容路径别名。"""
        return self.long_term_path

    def ensure_file(self) -> None:
        """创建长期记忆文件，并补齐所有必需的作用域标题。"""
        self.long_term_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.long_term_path.exists():
            self.long_term_path.write_text(SKELETON, encoding="utf-8")

    def ensure_daily_file(self, day: str | None = None) -> Path:
        """创建并返回指定日期的日记忆文件。

        日记忆文件与长期记忆分开存放，这样模型观察和推断事实可以先低成本落盘，
        不会直接污染更稳定的长期记忆。
        """
        day = day or date.today().isoformat()
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        path = self.daily_dir / f"{day}.md"
        if not path.exists():
            path.write_text(f"# Daily Memory {day}\n", encoding="utf-8")
        return path

    def read_text(self) -> str:
        return self.read_long_term_text()

    def read_long_term_text(self) -> str:
        """读取完整的长期记忆 Markdown 文件。"""
        self.ensure_file()
        return self.long_term_path.read_text(encoding="utf-8", errors="replace")

    def read_daily_text(self, day: str | None = None) -> str:
        """读取指定日期的日记忆 Markdown 文件；文件不存在时会先创建。"""
        path = self.ensure_daily_file(day)
        return path.read_text(encoding="utf-8", errors="replace")

    def add_text(self, text: str, source: str, reason: str) -> MemoryWriteResult:
        return self.add_long_term(text, source, reason)

    def write_with_policy(
        self,
        text: str,
        source: str,
        reason: str | None = None,
    ) -> MemoryWriteResult:
        """按照保留策略决定写入长期记忆还是日记忆。

        只有用户明确要求“记住”的内容才允许直接进入长期记忆。模型观察和模型推断
        的事实会先进入日记忆，后续只有在反复被召回时，才有理由晋升到长期记忆。
        """
        if source == "explicit_user_request":
            return self.add_long_term(
                text=text,
                source=source,
                reason=str(reason or ""),
            )
        return self.add_daily(text=text, source=source)

    def add_long_term(
        self,
        text: str,
        source: str,
        reason: str,
        promoted_from: str | None = None,
    ) -> MemoryWriteResult:
        """校验、追加并索引一条长期记忆。

        校验放在生成 ID 和写文件之前，确保被拦截的写入不会留下半成品状态。
        Markdown 更新后，会在同一个锁保护区间内同步 upsert 派生的 SQLite 索引。
        """
        source = str(source).strip()
        text = _normalize_inline_text(text)
        reason = _normalize_inline_text(reason)
        blocked_reason = self._validate_long_term_write(text, source, reason)
        if blocked_reason:
            return MemoryWriteResult(blocked=True, reason=blocked_reason)

        item = MemoryItem(
            id=str(uuid.uuid4()),
            tier="long_term",
            created_at=_now(),
            source=source,
            reason=reason,
            text=text,
            promoted_from=promoted_from,
        )
        with self._lock:
            # Markdown 始终是真源；随后立即更新 SQLite，让搜索能看到新记忆。
            current = self.read_long_term_text()
            updated = _append_long_term_item(current, item)
            self.long_term_path.write_text(updated, encoding="utf-8")
            self._upsert_index_item(item)
        return MemoryWriteResult(id=item.id)

    def add_daily(self, text: str, source: str, day: str | None = None) -> MemoryWriteResult:
        """校验、追加并索引一条日记忆。

        日记忆用于保存置信度较低或偏临时的信息。召回次数会和日记忆一起保存，
        这样晋升长期记忆时可以依据真实检索频率，而不是只凭单次观察。
        """
        source = str(source).strip()
        text = _normalize_inline_text(text)
        blocked_reason = self._validate_daily_write(text, source)
        if blocked_reason:
            return MemoryWriteResult(blocked=True, reason=blocked_reason)

        day = day or date.today().isoformat()
        item = MemoryItem(
            id=str(uuid.uuid4()),
            tier="daily",
            created_at=_now(),
            source=source,
            text=text,
            daily_date=day,
        )
        with self._lock:
            path = self.ensure_daily_file(day)
            current = path.read_text(encoding="utf-8", errors="replace")
            path.write_text(_append_daily_item(current, item), encoding="utf-8")
            self._upsert_index_item(item)
        return MemoryWriteResult(id=item.id)

    def search(
        self,
        query: str,
        tier: str | None = None,
        max_results: int | None = None,
        max_snippet_chars: int | None = None,
    ) -> list[MemorySearchResult]:
        """按关键词搜索记忆，并在配置允许时叠加向量相似度搜索。

        FTS 和向量搜索会分别执行。混合模式按记忆 ID 合并两边结果并保留最高分；
        非混合模式优先返回关键词结果，只有关键词没有命中时才回退到向量结果。
        搜索完成后，命中的日记忆会被计入一次召回。
        """
        query = str(query or "").strip()
        if not query:
            return []
        if tier and tier not in VALID_TIERS:
            return []

        max_results = max_results or self.config.search_max_results
        max_snippet_chars = max_snippet_chars or self.config.search_max_snippet_chars

        fts_results = self._fts_search(query, tier=tier)
        vector_results = self._vector_search(query, tier=tier)
        # 混合搜索是尽力而为：没有向量索引时关键词搜索仍可工作，向量不可用也不影响读取。
        if self.config.search_hybrid_enabled:
            merged = _merge_results(fts_results, vector_results)
        elif fts_results:
            merged = [(item, score, "fts5") for item, score in fts_results]
        else:
            merged = [(item, score, "vector") for item, score in vector_results]
        results = [
            MemorySearchResult(
                item=item,
                score=score,
                snippet=_truncate(item.text, max_snippet_chars),
                matched_by=matched_by,
            )
            for item, score, matched_by in merged[:max_results]
        ]
        self._record_daily_recalls([r.item for r in results if r.item.tier == "daily"])
        return results

    def promote_daily(self, item_id: str, reason: str) -> str:
        """将被反复召回的日记忆复制到长期记忆。

        晋升比普通日记忆写入更严格：调用方必须提供合法的长期作用域和 reason，
        并且该日记忆必须达到配置中的召回阈值。原日记忆不会删除，而是通过
        promoted_to 字段关联到新生成的长期记忆 ID。
        """
        if not self.config.promotion_enabled:
            raise ValueError("promotion_disabled")
        reason = _normalize_inline_text(reason)
        if not reason:
            raise ValueError("missing_reason")

        item = next((i for i in self._read_all_items() if i.id == item_id and i.tier == "daily"), None)
        if item is None:
            raise KeyError(item_id)
        if item.promoted_to:
            return item.promoted_to
        if item.recall_count < self.config.promotion_min_recall_count:
            raise ValueError("promotion_threshold_not_met")

        write = self.add_long_term(
            text=item.text,
            source="promoted_daily_memory",
            reason=reason,
            promoted_from=f"daily/{item.daily_date}.md#{item.id}",
        )
        if write.blocked or not write.id:
            raise ValueError(write.reason or "promotion_failed")
        self._update_daily_item(item.id, promoted_to=write.id)
        return write.id

    def rebuild_index(self) -> None:
        """清空派生搜索数据，并从 Markdown 真源文件完整重建索引。"""
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM memory_entries")
                conn.execute("DELETE FROM memory_fts")
                conn.execute("DELETE FROM memory_vectors")
                if self._load_sqlite_vec(conn):
                    conn.execute("DROP TABLE IF EXISTS memory_vec")
                self._vec_available = False
                self._vector_dimensions = None
                conn.execute("DELETE FROM memory_index_meta WHERE key IN ('embedding_dimensions', 'embedding_provider', 'embedding_model')")
                conn.commit()
            for item in self._read_all_items():
                self._upsert_index_item(item)

    def index_status(self) -> dict[str, object]:
        """暴露当前索引和搜索后端状态，方便诊断。"""
        return {
            "sqlite_path": str(self.index_path),
            "fts5_enabled": self._fts_available,
            "sqlite_vec_enabled": self._vec_available,
            "keyword_tokenizer": "jieba" if jieba is not None else "regex_fallback",
            "embedding_provider": self.config.embedding_provider,
            "embedding_model": self.config.embedding_model,
            "embedding_dimensions": self._vector_dimensions,
            "embedding_last_error": self._embedding_last_error,
        }

    def _read_items(self) -> list[MemoryItem]:
        """将长期记忆 Markdown 解析为 MemoryItem 列表。"""
        return _parse_long_term_items(self.read_long_term_text())

    def _read_all_items(self) -> list[MemoryItem]:
        """将长期记忆和所有日记忆 Markdown 合并解析为同一个 MemoryItem 列表。"""
        items = self._read_items()
        if self.daily_dir.exists():
            for path in sorted(self.daily_dir.glob("*.md")):
                items.extend(_parse_daily_items(path.read_text(encoding="utf-8", errors="replace"), path.stem))
        return items

    def _validate_long_term_write(self, text: str, source: str, reason: str) -> str | None:
        """校验长期记忆写入；不合法时返回拦截原因，否则返回 None。"""
        if source not in {"explicit_user_request", "promoted_daily_memory"}:
            return "source_invalid"
        if source == "explicit_user_request" and not self.config.admission_explicit_user_intent:
            return "source_disabled"
        if source == "model_inferred_stable_fact" and not self.config.admission_model_inferred_stable_fact:
            return "source_disabled"
        if not reason:
            return "missing_reason"
        return self._validate_common_text(text, reason)

    def _validate_daily_write(self, text: str, source: str) -> str | None:
        """校验日记忆写入；不合法时返回拦截原因，否则返回 None。"""
        if source not in {"explicit_user_request", "model_observation", "model_inferred_stable_fact"}:
            return "source_invalid"
        return self._validate_common_text(text, "")

    def _validate_common_text(self, text: str, reason: str) -> str | None:
        """执行长期记忆和日记忆共享的文本校验。

        这里防止空文本、超长文本和配置中的敏感模式进入记忆文件，例如凭据或 API key。
        """
        if not text:
            return "missing_text"
        if len(text) > self.config.write_max_text_chars:
            return "too_long"
        for pattern in self.config.sensitive_patterns:
            if re.search(pattern, text) or (reason and re.search(pattern, reason)):
                return "sensitive_pattern"
        return None

    def _ensure_index(self) -> None:
        """创建 SQLite 表，并探测可选搜索能力。

        FTS5 和 sqlite-vec 都是运行时可选能力。这里会记录它们是否可用；
        即使扩展加载失败，记忆写入和基础关键词搜索也应该继续工作。
        """
        self.root_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_entries (
                    id TEXT PRIMARY KEY,
                    tier TEXT NOT NULL,
                    scope TEXT,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    reason TEXT,
                    text TEXT NOT NULL,
                    daily_date TEXT,
                    recall_count INTEGER NOT NULL DEFAULT 0,
                    promoted_from TEXT,
                    promoted_to TEXT
                )
                """
            )
            try:
                # 如果当前 Python SQLite 构建包含 FTS5，就优先使用它做带排名的关键词搜索。
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                    USING fts5(item_id UNINDEXED, text, reason)
                    """
                )
                self._fts_available = True
            except sqlite3.OperationalError:
                # 如果 SQLite 没有编译 FTS5，则退回普通表；后续用 LIKE 谓词维持可用性。
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memory_fts (
                        item_id TEXT PRIMARY KEY,
                        text TEXT NOT NULL,
                        reason TEXT
                    )
                    """
                )
                self._fts_available = False
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_vectors (
                    item_id TEXT PRIMARY KEY,
                    embedding TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            meta = dict(conn.execute("SELECT key, value FROM memory_index_meta").fetchall())
            self._vector_dimensions = int(meta["embedding_dimensions"]) if "embedding_dimensions" in meta else None
            embedding_config_changed = (
                ("embedding_provider" in meta and meta["embedding_provider"] != self.config.embedding_provider)
                or ("embedding_model" in meta and meta["embedding_model"] != self.config.embedding_model)
            )
            if embedding_config_changed:
                # 现有向量来自不同 provider/model，继续使用会混淆相似度；要求重建索引。
                self._embedding_last_error = "embedding_config_changed_reindex_required"
            elif self._vector_dimensions and self._load_sqlite_vec(conn):
                try:
                    conn.execute(
                        f"""
                        CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec
                        USING vec0(embedding float[{self._vector_dimensions}])
                        """
                    )
                    self._vec_available = True
                except Exception as e:
                    self._vec_available = False
                    self._embedding_last_error = str(e)

    def _index_is_empty(self) -> bool:
        """当派生的 SQLite 入口表没有任何记录时返回 True。"""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM memory_entries").fetchone()
        return bool(row and row["c"] == 0)

    def _connect(self) -> sqlite3.Connection:
        """打开一个与当前 store 配置一致的 SQLite 连接。"""
        conn = sqlite3.connect(self.index_path)
        conn.row_factory = sqlite3.Row
        if self._vec_available:
            self._load_sqlite_vec(conn)
        return conn

    def _load_sqlite_vec(self, conn: sqlite3.Connection) -> bool:
        """尝试在指定连接上加载 sqlite-vec，并返回是否加载成功。"""
        try:
            import sqlite_vec  # type: ignore

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            return True
        except Exception:
            try:
                conn.enable_load_extension(False)
            except Exception:
                pass
            return False

    def _upsert_index_item(self, item: MemoryItem) -> None:
        """将一条记忆插入或替换到所有派生索引表中。

        普通元数据表始终会更新。关键词索引始终会更新为 FTS5 行或 fallback 行。
        只有在能生成 embedding 且 sqlite-vec 可用时，才会更新向量相关表。
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_entries (
                    id, tier, scope, created_at, source, reason, text, daily_date,
                    recall_count, promoted_from, promoted_to
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    tier=excluded.tier,
                    scope=excluded.scope,
                    created_at=excluded.created_at,
                    source=excluded.source,
                    reason=excluded.reason,
                    text=excluded.text,
                    daily_date=excluded.daily_date,
                    recall_count=excluded.recall_count,
                    promoted_from=excluded.promoted_from,
                    promoted_to=excluded.promoted_to
                """,
                (
                    item.id,
                    item.tier,
                    None,
                    item.created_at,
                    item.source,
                    item.reason,
                    item.text,
                    item.daily_date,
                    item.recall_count,
                    item.promoted_from,
                    item.promoted_to,
                ),
            )
            conn.execute("DELETE FROM memory_fts WHERE item_id = ?", (item.id,))
            # 索引中保存分词后的文本，而不是原始 Markdown 文本，保证中文分词和正则
            # 回退分词的行为尽量一致。
            conn.execute(
                "INSERT INTO memory_fts (item_id, text, reason) VALUES (?, ?, ?)",
                (
                    item.id,
                    _tokenize_for_search(item.text),
                    _tokenize_for_search(item.reason or ""),
                ),
            )
            conn.execute(
                "INSERT OR REPLACE INTO memory_index_meta (key, value) VALUES (?, ?)",
                ("keyword_tokenizer", "jieba" if jieba is not None else "regex_fallback"),
            )
            embedding = self._embed_text(item.text)
            if embedding is not None:
                conn.execute(
                    """
                    INSERT INTO memory_vectors (item_id, embedding) VALUES (?, ?)
                    ON CONFLICT(item_id) DO UPDATE SET embedding=excluded.embedding
                    """,
                    (item.id, json.dumps(embedding)),
                )
                self._ensure_vec_table(conn, len(embedding))
            if embedding is not None and self._vec_available:
                # sqlite-vec 使用 memory_entries 的 rowid 作为关联键，因此必须先完成
                # 元数据 upsert，再查询 rowid 写入向量表。
                rowid = conn.execute("SELECT rowid FROM memory_entries WHERE id = ?", (item.id,)).fetchone()["rowid"]
                conn.execute("DELETE FROM memory_vec WHERE rowid = ?", (rowid,))
                conn.execute(
                    "INSERT INTO memory_vec (rowid, embedding) VALUES (?, ?)",
                    (rowid, json.dumps(embedding)),
                )
            conn.commit()

    def _fts_search(self, query: str, tier: str | None) -> list[tuple[MemoryItem, float]]:
        """通过 FTS5 或基于 LIKE 的 fallback 执行关键词搜索。"""
        terms = _terms(query)
        if not terms or not self.config.search_keyword_enabled:
            return []
        if self._fts_available:
            # 构造 MATCH 表达式前先给每个 term 加引号，避免用户输入标点或引号时破坏 FTS 语法。
            match_expr = " OR ".join(_fts_quote(term) for term in terms)
            sql = """
                SELECT e.*, bm25(memory_fts) AS rank
                FROM memory_fts
                JOIN memory_entries e ON e.id = memory_fts.item_id
                WHERE memory_fts MATCH ?
            """
            params: list[str] = [match_expr]
        else:
            # 回退搜索精度较低，但能保证缺少 FTS5 的环境仍然可以检索记忆。
            predicates = " OR ".join("(LOWER(memory_fts.text) LIKE ? OR LOWER(memory_fts.reason) LIKE ?)" for _ in terms)
            sql = f"""
                SELECT e.*, 0.0 AS rank
                FROM memory_fts
                JOIN memory_entries e ON e.id = memory_fts.item_id
                WHERE {predicates}
            """
            params = []
            for term in terms:
                like = f"%{term}%"
                params.extend([like, like])
        if tier:
            sql += " AND e.tier = ?"
            params.append(tier)
        sql += " ORDER BY rank LIMIT ?"
        params.append(str(max(self.config.search_max_results * 4, 10)))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [(_row_to_item(row), 1.0 / (1.0 + abs(float(row["rank"])))) for row in rows]

    def _vector_search(self, query: str, tier: str | None) -> list[tuple[MemoryItem, float]]:
        """使用 sqlite-vec 对记忆执行向量近邻搜索。"""
        # sqlite-vec 是可选依赖；不可用时直接返回空结果，让上层依赖关键词搜索兜底。
        if not self.config.search_vector_enabled:
            return []
        query_vec = self._embed_text(query)
        if query_vec is None or not self._vec_available:
            return []
        if self._vec_available:
            sql = """
                WITH nearest AS (
                    SELECT rowid, distance
                    FROM memory_vec
                    WHERE embedding MATCH ? AND k = ?
                )
                SELECT e.*, nearest.distance
                FROM nearest
                JOIN memory_entries e ON e.rowid = nearest.rowid
                WHERE 1 = 1
            """
            params: list[str] = [json.dumps(query_vec), str(max(self.config.search_max_results * 4, 10))]
            if tier:
                sql += " AND e.tier = ?"
                params.append(tier)
            sql += " ORDER BY distance"
            with self._connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            return [(_row_to_item(row), 1.0 / (1.0 + float(row["distance"]))) for row in rows]

        return []

    def _embed_text(self, text: str) -> list[float] | None:
        """向配置的 embedding provider 请求文本向量。

        返回 None 表示本次操作应跳过向量搜索。最近一次错误会保留下来用于诊断，
        但 embedding 失败不会阻断 Markdown 写入或关键词搜索。
        """
        if not self.config.search_vector_enabled or self.config.embedding_provider == "none":
            return None
        if self.config.embedding_provider != "ollama":
            self._embedding_last_error = f"unsupported_embedding_provider:{self.config.embedding_provider}"
            return None
        base_url = self.config.embedding_base_url.rstrip("/")
        body = json.dumps({"model": self.config.embedding_model, "input": text}).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.embedding_timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            embeddings = payload.get("embeddings") or []
            if not embeddings or not isinstance(embeddings[0], list):
                raise ValueError("ollama_response_missing_embeddings")
            vector = [float(v) for v in embeddings[0]]
            self._embedding_last_error = None
            return vector
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError) as e:
            self._embedding_last_error = str(e)
            return None

    def _ensure_vec_table(self, conn: sqlite3.Connection, dimensions: int) -> None:
        """确保 sqlite-vec 表结构与当前 embedding 维度一致。

        sqlite-vec 要求在表定义中写死向量维度。embedding 模型变化可能导致已有向量
        维度不兼容；这种情况下会禁用向量搜索，直到执行完整 reindex。
        """
        if self._vector_dimensions is None:
            if not self._load_sqlite_vec(conn):
                self._vec_available = False
                return
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec
                USING vec0(embedding float[{dimensions}])
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO memory_index_meta (key, value) VALUES (?, ?)",
                ("embedding_dimensions", str(dimensions)),
            )
            conn.execute(
                "INSERT OR REPLACE INTO memory_index_meta (key, value) VALUES (?, ?)",
                ("embedding_provider", self.config.embedding_provider),
            )
            conn.execute(
                "INSERT OR REPLACE INTO memory_index_meta (key, value) VALUES (?, ?)",
                ("embedding_model", self.config.embedding_model),
            )
            self._vector_dimensions = dimensions
            self._vec_available = True
            return
        if self._vector_dimensions != dimensions:
            self._embedding_last_error = "embedding_dimensions_changed_reindex_required"
            self._vec_available = False
            return
        conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec
            USING vec0(embedding float[{dimensions}])
            """
        )
        self._vec_available = True

    def _record_daily_recalls(self, items: list[MemoryItem]) -> None:
        """为搜索命中的日记忆递增召回计数。"""
        for item in items:
            self._update_daily_item(item.id, recall_count=item.recall_count + 1)

    def _update_daily_item(self, item_id: str, **changes: object) -> None:
        """重写某条日记忆块，并同步更新它在派生索引中的记录。"""
        all_items = self._read_all_items()
        item = next((i for i in all_items if i.id == item_id and i.tier == "daily"), None)
        if item is None or not item.daily_date:
            return
        updated = MemoryItem(
            **{
                **item.__dict__,
                **changes,
            }
        )
        path = self.ensure_daily_file(item.daily_date)
        text = path.read_text(encoding="utf-8", errors="replace")
        path.write_text(_replace_daily_item(text, updated), encoding="utf-8")
        self._upsert_index_item(updated)


def build_memory_store(config: AgentConfig) -> MarkdownMemoryStore | None:
    """根据总配置构建记忆存储；禁用记忆功能时返回 None。"""
    if not config.memory.enabled:
        return None
    return MarkdownMemoryStore(config.resolved_memory_file(), config.memory)


def _append_long_term_item(text: str, item: MemoryItem) -> str:
    """把长期记忆追加到 MEMORY.md 末尾，并保持 Markdown 文件结构稳定。

    这个函数只负责文本拼接，不做策略校验。调用前应已经确认 source/reason 合法。
    长期记忆不再按 scope 分区，所有条目按追加顺序平铺在根标题下。
    """
    text = _ensure_skeleton_sections(text)
    lines = text.splitlines()
    insert_at = len(lines)
    block = [
        f"- id: {item.id}",
        f"  created_at: {item.created_at}",
        f"  source: {item.source}",
        f"  reason: {item.reason}",
    ]
    if item.promoted_from:
        # 晋升来的长期记忆保留来源，方便追溯它原本来自哪一天的日记忆。
        block.append(f"  promoted_from: {item.promoted_from}")
    block.append(f"  text: {item.text}")
    if insert_at > 0 and lines[insert_at - 1].strip():
        # 与前一个块保持一个空行，提升用户手工阅读和编辑体验。
        block.insert(0, "")
    if insert_at < len(lines) and lines[insert_at].strip():
        block.append("")
    lines[insert_at:insert_at] = block
    return "\n".join(lines).rstrip() + "\n"


def _append_daily_item(text: str, item: MemoryItem) -> str:
    """把一条日记忆追加到当天 Markdown 文件末尾。"""
    lines = text.splitlines()
    block = _daily_block(item)
    if lines and lines[-1].strip():
        lines.append("")
    lines.extend(block)
    return "\n".join(lines).rstrip() + "\n"


def _replace_daily_item(text: str, updated: MemoryItem) -> str:
    """用更新后的 item 重写对应日记忆文件。

    目前日记忆文件体量较小，因此直接解析全部条目并重新生成文件，比做局部字符串
    替换更可靠，也能顺带把文件格式规整回标准块结构。
    """
    items = _parse_daily_items(text, updated.daily_date or "")
    lines = [f"# Daily Memory {updated.daily_date}"]
    for item in items:
        if item.id == updated.id:
            item = updated
        lines.append("")
        lines.extend(_daily_block(item))
    return "\n".join(lines).rstrip() + "\n"


def _daily_block(item: MemoryItem) -> list[str]:
    """生成日记忆在 Markdown 中的标准块格式。"""
    return [
        f"- id: {item.id}",
        f"  created_at: {item.created_at}",
        f"  source: {item.source}",
        f"  text: {item.text}",
        f"  recall_count: {item.recall_count}",
        f"  promoted_to: {item.promoted_to or ''}",
    ]


def _ensure_skeleton_sections(text: str) -> str:
    """确保长期记忆 Markdown 包含根标题。

    用户可能手工编辑或清空 MEMORY.md。追加记忆前先补齐根标题，可以避免文件缺少
    基本结构。长期记忆不再按 scope 分节。
    """
    if not text.strip():
        return SKELETON
    lines = text.splitlines()
    if not lines or lines[0].strip() != "# Agent Memory":
        lines.insert(0, "# Agent Memory")
        lines.insert(1, "")
    return "\n".join(lines).rstrip() + "\n"


def _parse_long_term_items(text: str) -> list[MemoryItem]:
    """从长期记忆 Markdown 中解析出 MemoryItem 列表。

    解析器只识别当前约定的简单块格式：``- id:`` 开始一条记录，后续两个空格缩进的
    ``key: value`` 行作为字段。字段不完整的块会被跳过，避免手工编辑产生的半条记录
    进入索引。
    """
    items: list[MemoryItem] = []
    current: dict[str, str] | None = None

    def flush() -> None:
        """在遇到新标题或新记录时，把当前累计字段落成一条 MemoryItem。"""
        nonlocal current
        if not current:
            current = None
            return
        required = {"id", "created_at", "source", "reason", "text"}
        if required <= set(current):
            items.append(MemoryItem(
                id=current["id"],
                tier="long_term",
                created_at=current["created_at"],
                source=current["source"],
                reason=current["reason"],
                text=current["text"],
                promoted_from=current.get("promoted_from"),
            ))
        current = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            flush()
            continue
        if line.startswith("- id:"):
            flush()
            current = {"id": line.split(":", 1)[1].strip()}
            continue
        if current is not None and line.startswith("  ") and ":" in line:
            key, value = line.strip().split(":", 1)
            current[key.strip()] = value.strip()
    flush()
    return items


def _parse_daily_items(text: str, daily_date: str) -> list[MemoryItem]:
    """从某一天的日记忆 Markdown 中解析出 MemoryItem 列表。

    daily_date 来自文件名或调用方上下文，不依赖文件标题；这样即使标题被用户改坏，
    仍能从路径恢复日期信息。
    """
    items: list[MemoryItem] = []
    current: dict[str, str] | None = None

    def flush() -> None:
        """把当前累计字段转换成日记忆条目；字段不足时静默丢弃。"""
        nonlocal current
        if not current:
            return
        required = {"id", "created_at", "source", "text"}
        if required <= set(current):
            items.append(MemoryItem(
                id=current["id"],
                tier="daily",
                created_at=current["created_at"],
                source=current["source"],
                text=current["text"],
                daily_date=daily_date,
                recall_count=int(current.get("recall_count", "0") or 0),
                promoted_to=current.get("promoted_to") or None,
            ))
        current = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("- id:"):
            flush()
            current = {"id": line.split(":", 1)[1].strip()}
            continue
        if current is not None and line.startswith("  ") and ":" in line:
            key, value = line.strip().split(":", 1)
            current[key.strip()] = value.strip()
    flush()
    return items


def _merge_results(
    fts_results: list[tuple[MemoryItem, float]],
    vector_results: list[tuple[MemoryItem, float]],
) -> list[tuple[MemoryItem, float, str]]:
    """合并关键词搜索和向量搜索结果。

    同一条记忆可能同时被两种方式命中。合并时按 ID 去重，分数取两者最大值，并用
    matched_by 标记结果来自 fts5、vector 还是 hybrid。
    """
    merged: dict[str, tuple[MemoryItem, float, set[str]]] = {}
    for item, score in fts_results:
        merged[item.id] = (item, score, {"fts5"})
    for item, score in vector_results:
        if item.id in merged:
            existing_item, existing_score, labels = merged[item.id]
            merged[item.id] = (existing_item, max(existing_score, score), labels | {"vector"})
        else:
            merged[item.id] = (item, score, {"vector"})
    result = []
    for item, score, labels in merged.values():
        matched_by = (
            "hybrid" if labels == {"fts5", "vector"} else next(iter(labels))  # type: ignore[assignment]
        )
        result.append((item, score, matched_by))
    result.sort(key=lambda row: (-row[1], row[0].created_at, row[0].id))
    return result


def _row_to_item(row: sqlite3.Row) -> MemoryItem:
    """把 SQLite 查询行还原成 MemoryItem。"""
    return MemoryItem(
        id=row["id"],
        tier=row["tier"],
        created_at=row["created_at"],
        source=row["source"],
        reason=row["reason"],
        text=row["text"],
        daily_date=row["daily_date"],
        recall_count=row["recall_count"],
        promoted_from=row["promoted_from"],
        promoted_to=row["promoted_to"],
    )


def _normalize_inline_text(value: str) -> str:
    """把输入压缩成单行文本，避免换行破坏当前 Markdown 块格式。"""
    return " ".join(str(value or "").split())


def _terms(query: str) -> list[str]:
    """提取查询词；保留这个薄封装便于调用处表达语义。"""
    return _tokenize_terms(query)


def _tokenize_for_search(text: str) -> str:
    """把原始文本转换成写入关键词索引的空格分隔 token 串。"""
    return " ".join(_tokenize_terms(text))


def _tokenize_terms(text: str) -> list[str]:
    """从文本中提取去重后的搜索 token。

    中文优先使用 jieba 的搜索模式分词；英文、数字、路径和常见标识符再通过正则补充。
    最后按出现顺序去重，避免 FTS 表里重复 token 影响索引体积。
    """
    text = str(text or "")
    terms: list[str] = []
    if jieba is not None:
        terms.extend(token.strip().lower() for token in jieba.cut_for_search(text) if _is_search_term(token))
    terms.extend(term.lower() for term in re.findall(r"[A-Za-z0-9_./:-]+", text, flags=re.UNICODE))
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            result.append(term)
    return result


def _is_search_term(token: str) -> bool:
    """判断 jieba token 是否包含可检索字符。"""
    token = token.strip()
    return bool(token and re.search(r"[A-Za-z0-9_\u4e00-\u9fff]", token))


def _fts_quote(term: str) -> str:
    """把单个查询词转成安全的 FTS MATCH 片段。"""
    return '"' + term.replace('"', '""') + '"'


def _truncate(text: str, limit: int) -> str:
    """按最大字符数截断搜索摘要，必要时追加省略号。"""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _now() -> str:
    """返回 UTC ISO 时间戳，作为记忆创建时间。"""
    return datetime.now(timezone.utc).isoformat()


def format_search_results(results: Iterable[MemorySearchResult]) -> list[dict]:
    """把内部搜索结果转换成工具层返回的 JSON 友好结构。"""
    return [
        {
            "id": result.item.id,
            "tier": result.item.tier,
            "score": result.score,
            "snippet": result.snippet,
            "source": result.item.source,
            "created_at": result.item.created_at,
            "matched_by": result.matched_by,
            "daily_date": result.item.daily_date,
        }
        for result in results
    ]
