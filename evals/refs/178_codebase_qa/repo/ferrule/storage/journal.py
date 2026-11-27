"""Journaling decorator: every write goes to the WAL before the backend."""

from __future__ import annotations

import json

from ferrule.jobs.model import Job
from ferrule.storage.base import Store, StoreWrapper
from ferrule.storage.wal import WalWriter


class JournaledStore(StoreWrapper):
    def __init__(self, inner: Store, writer: WalWriter) -> None:
        super().__init__(inner)
        self.writer = writer

    def put(self, job: Job) -> None:
        self.writer.append(json.dumps({"op": "put", "job": job.to_dict()}).encode())
        self.inner.put(job)

    def delete(self, job_id: str) -> bool:
        self.writer.append(json.dumps({"op": "delete", "id": job_id}).encode())
        return self.inner.delete(job_id)

    def close(self) -> None:
        self.writer.close()
        super().close()
