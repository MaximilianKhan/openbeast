"""The Store interface."""

from __future__ import annotations

from typing import Iterator, List, Optional

from ferrule.jobs.model import Job


class Store:
    """Persistence for Job records, keyed by job id.

    ``put`` inserts or replaces. Implementations must return copies from
    ``get``/``scan`` or treat stored objects as owned by the caller; the
    in-memory store does the latter.
    """

    name = "abstract"

    def put(self, job: Job) -> None:
        raise NotImplementedError

    def get(self, job_id: str) -> Optional[Job]:
        raise NotImplementedError

    def delete(self, job_id: str) -> bool:
        raise NotImplementedError

    def scan(self) -> Iterator[Job]:
        raise NotImplementedError

    def by_state(self, state: str) -> List[Job]:
        return [job for job in self.scan() if job.state == state]

    def count(self) -> int:
        return sum(1 for _ in self.scan())

    def close(self) -> None:
        pass


class StoreWrapper(Store):
    """Base for stores that decorate another store."""

    def __init__(self, inner: Store) -> None:
        self.inner = inner

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"{type(self).__name__.lower()}({self.inner.name})"

    def put(self, job: Job) -> None:
        self.inner.put(job)

    def get(self, job_id: str) -> Optional[Job]:
        return self.inner.get(job_id)

    def delete(self, job_id: str) -> bool:
        return self.inner.delete(job_id)

    def scan(self) -> Iterator[Job]:
        return self.inner.scan()

    def close(self) -> None:
        self.inner.close()
