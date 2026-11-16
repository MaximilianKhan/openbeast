"""Read-through cache in front of a store."""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional

from ferrule.jobs.model import Job
from ferrule.storage.base import Store, StoreWrapper


class CachedStore(StoreWrapper):
    def __init__(self, inner: Store, size: int = 4096) -> None:
        super().__init__(inner)
        self.size = size
        self._lru: "OrderedDict[str, Job]" = OrderedDict()

    def _remember(self, job: Job) -> None:
        self._lru[job.id] = job
        self._lru.move_to_end(job.id)
        while len(self._lru) > self.size:
            self._lru.popitem(last=False)

    def put(self, job: Job) -> None:
        self.inner.put(job)
        self._remember(job)

    def get(self, job_id: str) -> Optional[Job]:
        if job_id in self._lru:
            self._lru.move_to_end(job_id)
            return self._lru[job_id]
        job = self.inner.get(job_id)
        if job is not None:
            self._remember(job)
        return job

    def delete(self, job_id: str) -> bool:
        self._lru.pop(job_id, None)
        return self.inner.delete(job_id)
