from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class RuntimeStore:
    """Durable operational state; creative project files remain on disk."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _init(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_events_created_at ON events(created_at);
                """
            )

    def upsert_run(self, run: dict[str, Any]) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO runs(id,payload,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                (run["id"], json.dumps(run, ensure_ascii=False, default=str), time.time()),
            )

    def load_runs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [json.loads(row["payload"]) for row in db.execute(
                "SELECT payload FROM runs ORDER BY updated_at"
            )]

    def upsert_approval(self, approval: dict[str, Any]) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO approvals(id,payload,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                (approval["id"], json.dumps(approval, ensure_ascii=False, default=str), time.time()),
            )

    def load_approvals(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [json.loads(row["payload"]) for row in db.execute(
                "SELECT payload FROM approvals ORDER BY updated_at"
            )]

    def append_event(self, event: dict[str, Any]) -> int:
        event_type = str(event.get("type") or "message")
        with self._lock, self._connect() as db:
            cur = db.execute(
                "INSERT INTO events(event_type,payload,created_at) VALUES(?,?,?)",
                (event_type, json.dumps(event, ensure_ascii=False, default=str), time.time()),
            )
            sequence = int(cur.lastrowid)
        return sequence

    def events_after(self, sequence: int, limit: int = 1000) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT sequence,payload FROM events WHERE sequence>? ORDER BY sequence LIMIT ?",
                (sequence, limit),
            )
            result = []
            for row in rows:
                payload = json.loads(row["payload"])
                payload["sequence"] = int(row["sequence"])
                result.append(payload)
            return result
