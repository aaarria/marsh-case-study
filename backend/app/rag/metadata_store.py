"""SQLite metadata store (chunks, policies, run artefacts, caches).

Single-file, dependency-free persistence. Table layout is deliberately simple so it can be mirrored
in PostgreSQL later.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from app.config import get_settings
from app.models.policy import Chunk, PolicyDocument

SCHEMA = """
CREATE TABLE IF NOT EXISTS policies (
    policy_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    section TEXT,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_policy ON chunks(policy_id);
CREATE INDEX IF NOT EXISTS idx_chunks_type ON chunks(policy_id, content_type);
CREATE TABLE IF NOT EXISTS kv_cache (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (namespace, key)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    node TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id);
CREATE TABLE IF NOT EXISTS artifacts (
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, kind)
);
"""


class SQLiteMetadataStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or get_settings().sqlite_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    # ---- policies ----
    def upsert_policies(self, docs: Sequence[PolicyDocument]) -> None:
        with self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO policies(policy_id, data) VALUES (?, ?)",
                [(d.policy_id, d.model_dump_json()) for d in docs],
            )

    def list_policies(self) -> list[PolicyDocument]:
        with self._conn() as c:
            rows = c.execute("SELECT data FROM policies ORDER BY policy_id").fetchall()
        docs = [PolicyDocument.model_validate_json(r[0]) for r in rows]
        return sorted(docs, key=lambda d: d.short_label or d.policy_id)

    # ---- chunks ----
    def clear_chunks(self, policy_id: str | None = None) -> None:
        with self._conn() as c:
            if policy_id:
                c.execute("DELETE FROM chunks WHERE policy_id=?", (policy_id,))
            else:
                c.execute("DELETE FROM chunks")

    def upsert_chunks(self, chunks: Sequence[Chunk]) -> None:
        with self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO chunks(chunk_id, policy_id, page_number, content_type, section, data) VALUES (?,?,?,?,?,?)",
                [(ch.chunk_id, ch.policy_id, ch.page_number, ch.content_type.value, ch.section, ch.model_dump_json()) for ch in chunks],
            )

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        with self._conn() as c:
            row = c.execute("SELECT data FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
        return Chunk.model_validate_json(row[0]) if row else None

    def get_chunks(self, chunk_ids: Sequence[str]) -> list[Chunk]:
        ids = list(dict.fromkeys(chunk_ids))
        if not ids:
            return []
        out: dict[str, Chunk] = {}
        with self._conn() as c:
            for i in range(0, len(ids), 500):
                batch = ids[i : i + 500]
                q = f"SELECT chunk_id, data FROM chunks WHERE chunk_id IN ({','.join('?' * len(batch))})"
                for cid, data in c.execute(q, batch):
                    out[cid] = Chunk.model_validate_json(data)
        return [out[i] for i in ids if i in out]

    def list_chunks(self, policy_id: str | None = None, content_types: Sequence[str] | None = None) -> list[Chunk]:
        q = "SELECT data FROM chunks"
        conds, params = [], []
        if policy_id:
            conds.append("policy_id=?")
            params.append(policy_id)
        if content_types:
            conds.append(f"content_type IN ({','.join('?' * len(content_types))})")
            params.extend(content_types)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY policy_id, page_number"
        with self._conn() as c:
            rows = c.execute(q, params).fetchall()
        return [Chunk.model_validate_json(r[0]) for r in rows]

    def chunk_ids_for_policy(self, policy_id: str) -> set[str]:
        with self._conn() as c:
            rows = c.execute("SELECT chunk_id FROM chunks WHERE policy_id=?", (policy_id,)).fetchall()
        return {r[0] for r in rows}

    def count_chunks(self) -> int:
        with self._conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])

    # ---- generic cache ----
    def cache_get(self, namespace: str, key: str) -> Any | None:
        with self._conn() as c:
            row = c.execute("SELECT data FROM kv_cache WHERE namespace=? AND key=?", (namespace, key)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_set(self, namespace: str, key: str, value: Any) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO kv_cache(namespace, key, data) VALUES (?,?,?)",
                (namespace, key, json.dumps(value, default=str)),
            )

    def cache_clear(self, namespace: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM kv_cache WHERE namespace=?", (namespace,))

    # ---- runs ----
    def save_run(self, run_id: str, company_name: str, status: str, data: dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO runs(run_id, company_name, status, data) VALUES (?,?,?,?)
                   ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, data=excluded.data, updated_at=CURRENT_TIMESTAMP""",
                (run_id, company_name, status, json.dumps(data, default=str)),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT run_id, company_name, status, created_at, updated_at, data FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        return {"run_id": row[0], "company_name": row[1], "status": row[2], "created_at": row[3], "updated_at": row[4], **json.loads(row[5])}

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT run_id, company_name, status, created_at, updated_at, data FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            d = json.loads(r[5])
            out.append(
                {
                    "run_id": r[0],
                    "company_name": r[1],
                    "status": r[2],
                    "created_at": r[3],
                    "updated_at": r[4],
                    "recommended_policy_id": d.get("recommended_policy_id"),
                    "audit_gate": d.get("audit_gate"),
                    "current_node": d.get("current_node"),
                    "error": d.get("error"),
                    "error_kind": d.get("error_kind"),
                    "retry_after": d.get("retry_after"),
                    "retryable": d.get("retryable"),
                    "retries": d.get("retries"),
                }
            )
        return out

    def delete_run(self, run_id: str) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                return False
            c.execute("DELETE FROM artifacts WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM run_events WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
        return True

    def run_ids_with_status(self, status: str) -> list[str]:
        with self._conn() as c:
            rows = c.execute("SELECT run_id FROM runs WHERE status=?", (status,)).fetchall()
        return [r[0] for r in rows]

    def add_event(self, run_id: str, node: str, status: str, message: str | None = None) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO run_events(run_id, node, status, message) VALUES (?,?,?,?)", (run_id, node, status, message))

    def list_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT node, status, message, created_at FROM run_events WHERE run_id=? ORDER BY id", (run_id,)
            ).fetchall()
        return [{"node": r[0], "status": r[1], "message": r[2], "created_at": r[3]} for r in rows]

    def save_artifact(self, run_id: str, kind: str, data: Any) -> None:
        payload = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
        with self._conn() as c:
            c.execute(
                """INSERT INTO artifacts(run_id, kind, data) VALUES (?,?,?)
                   ON CONFLICT(run_id, kind) DO UPDATE SET data=excluded.data, updated_at=CURRENT_TIMESTAMP""",
                (run_id, kind, json.dumps(payload, default=str)),
            )

    def get_artifact(self, run_id: str, kind: str) -> Any | None:
        with self._conn() as c:
            row = c.execute("SELECT data FROM artifacts WHERE run_id=? AND kind=?", (run_id, kind)).fetchone()
        return json.loads(row[0]) if row else None
