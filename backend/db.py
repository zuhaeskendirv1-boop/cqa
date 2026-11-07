"""
SQLite document store: one table per collection, docs stored as JSON.
Safe for concurrent threads (WAL + per-connection), survives restarts, zero setup.
All access goes through these functions, so swapping to Postgres later is one file.
"""
from __future__ import annotations
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.getenv("CQA_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = Path(os.getenv("CQA_DB", DATA_DIR / "cqa.sqlite3"))
COLLECTIONS = ("checklists", "conversations", "evaluations", "appeals", "ground_truth", "jobs")
_lock = threading.RLock()
_local = threading.local()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        for t in COLLECTIONS:
            c.execute(f"CREATE TABLE IF NOT EXISTS {t} (id TEXT PRIMARY KEY, doc TEXT NOT NULL, created_at TEXT, updated_at TEXT)")
        _local.conn = c
    return c


def get_all(collection: str) -> list[dict]:
    assert collection in COLLECTIONS
    with _lock:
        rows = _conn().execute(f"SELECT doc FROM {collection} ORDER BY created_at").fetchall()
    return [json.loads(r[0]) for r in rows]


def get(collection: str, _id: str) -> dict | None:
    assert collection in COLLECTIONS
    with _lock:
        row = _conn().execute(f"SELECT doc FROM {collection} WHERE id=?", (_id,)).fetchone()
    return json.loads(row[0]) if row else None


def put(collection: str, _id: str, doc: dict) -> dict:
    assert collection in COLLECTIONS
    with _lock:
        old = get(collection, _id)
        doc = dict(doc)
        doc["created_at"] = (old or {}).get("created_at") or doc.get("created_at") or now()
        doc["updated_at"] = now()
        _conn().execute(
            f"INSERT INTO {collection}(id, doc, created_at, updated_at) VALUES(?,?,?,?) "
            f"ON CONFLICT(id) DO UPDATE SET doc=excluded.doc, updated_at=excluded.updated_at",
            (_id, json.dumps(doc, ensure_ascii=False), doc["created_at"], doc["updated_at"]),
        )
    return doc


def delete(collection: str, _id: str) -> bool:
    assert collection in COLLECTIONS
    with _lock:
        cur = _conn().execute(f"DELETE FROM {collection} WHERE id=?", (_id,))
    return cur.rowcount > 0


def count(collection: str) -> int:
    with _lock:
        return _conn().execute(f"SELECT COUNT(*) FROM {collection}").fetchone()[0]
