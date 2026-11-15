"""In-process store for tests and the dev profile."""

from __future__ import annotations

from typing import Dict, Iterator, Optional

from ferrule.jobs.model import Job
from ferrule.storage.base import Store


class MemoryStore(Store):
    name = "memory"

    def __init__(self, cfg=None) -> None:
        self._jobs: Dict[str, Job] = {}

    def put(self, job: Job) -> None:
        self._jobs[job.id] = job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        return self._jobs.pop(job_id, None) is not None

    def scan(self) -> Iterator[Job]:
        return iter(list(self._jobs.values()))
