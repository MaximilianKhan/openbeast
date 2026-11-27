"""Lease bookkeeping for workers that run jobs out of process.

The in-process scheduler leases and starts a job in the same tick, so it
never needs this. External workers (``ferrule worker``) lease a job, send
heartbeats while running it, and lose the lease if a heartbeat is late by
more than ``scheduler.lease_timeout``; ``LeaseTable.expired()`` reports
those jobs so the caller can apply the ``expire`` event.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

from ferrule.jobs.clock import Clock, SystemClock
from ferrule.util.durations import parse_duration


@dataclass
class Lease:
    job_id: str
    worker: str
    granted_ms: int
    last_beat_ms: int
    beats: int = 0


class LeaseTable:
    def __init__(self, settings, clock: Optional[Clock] = None) -> None:
        cfg = settings["scheduler"]
        self.timeout_ms = int(parse_duration(cfg["lease_timeout"]) * 1000)
        self.heartbeat_ms = int(parse_duration(cfg["heartbeat"]) * 1000)
        self.clock = clock or SystemClock()
        self._leases: Dict[str, Lease] = {}
        self._lock = threading.Lock()

    def grant(self, job_id: str, worker: str) -> Lease:
        now = self.clock.now_ms()
        with self._lock:
            if job_id in self._leases:
                raise KeyError(f"job {job_id} is already leased to {self._leases[job_id].worker}")
            lease = Lease(job_id, worker, now, now)
            self._leases[job_id] = lease
            return lease

    def beat(self, job_id: str, worker: str) -> bool:
        with self._lock:
            lease = self._leases.get(job_id)
            if lease is None or lease.worker != worker:
                return False
            lease.last_beat_ms = self.clock.now_ms()
            lease.beats += 1
            return True

    def release(self, job_id: str) -> Optional[Lease]:
        with self._lock:
            return self._leases.pop(job_id, None)

    def expired(self) -> List[str]:
        now = self.clock.now_ms()
        with self._lock:
            dead = [jid for jid, lease in self._leases.items() if now - lease.last_beat_ms > self.timeout_ms]
            for jid in dead:
                del self._leases[jid]
        return dead

    def holders(self) -> Dict[str, str]:
        with self._lock:
            return {jid: lease.worker for jid, lease in self._leases.items()}
