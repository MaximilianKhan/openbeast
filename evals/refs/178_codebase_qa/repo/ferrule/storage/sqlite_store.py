"""SQLite-backed store. One table, the job serialised as JSON."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Iterator, Optional

from ferrule.jobs.model import Job
from ferrule.storage.base import Store

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id      TEXT PRIMARY KEY,
    tenant  TEXT NOT NULL,
    state   TEXT NOT NULL,
    doc     TEXT NOT NULL
)
"""


class SqliteStore(Store):
    name = "sqlite"

    def __init__(self, cfg) -> None:
        sq = cfg["sqlite"]
        self.path = sq["path"]
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, timeout=sq["timeout_s"], check_same_thread=False)
        self._db.execute(_SCHEMA)

    def put(self, job: Job) -> None:
        doc = json.dumps(job.to_dict(), sort_keys=True)
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO jobs(id, tenant, state, doc) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET tenant=excluded.tenant, "
                "state=excluded.state, doc=excluded.doc",
                (job.id, job.tenant, job.state, doc),
            )

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            row = self._db.execute("SELECT doc FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_dict(json.loads(row[0])) if row else None

    def delete(self, job_id: str) -> bool:
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return cur.rowcount > 0

    def scan(self) -> Iterator[Job]:
        with self._lock:
            rows = self._db.execute("SELECT doc FROM jobs ORDER BY rowid").fetchall()
        return (Job.from_dict(json.loads(r[0])) for r in rows)

    def by_state(self, state: str):
        with self._lock:
            rows = self._db.execute("SELECT doc FROM jobs WHERE state = ? ORDER BY rowid",
                                    (state,)).fetchall()
        return [Job.from_dict(json.loads(r[0])) for r in rows]

    def close(self) -> None:
        self._db.close()
