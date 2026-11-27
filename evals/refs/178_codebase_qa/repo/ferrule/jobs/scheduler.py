"""The scheduler: leases queued jobs to workers and handles their outcome.

The scheduler is deliberately single-threaded; "workers" are slots in a
cooperative loop and ``self.workers`` is how many jobs may be RUNNING at
once. ``tick()`` does one round of: wake jobs whose retry delay elapsed,
lease queued jobs into free slots, run them.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from ferrule.constants import WORKERS_PER_SLOT
from ferrule.jobs.clock import Clock, SystemClock
from ferrule.jobs.kinds import is_control_plane
from ferrule.jobs.retry import RetryPolicy, policy_for_kind
from ferrule.jobs.retry_legacy import legacy_from
from ferrule.jobs.states import JobStateMachine
from ferrule.util.durations import parse_duration

log = logging.getLogger(__name__)

Handler = Callable[[object], None]


class Scheduler:
    PER_SLOT = WORKERS_PER_SLOT

    def __init__(self, settings, store, hooks=None, clock: Optional[Clock] = None) -> None:
        cfg = settings["scheduler"]
        self.settings = settings
        self.store = store
        self.hooks = hooks
        self.clock = clock or SystemClock()
        self.machine = JobStateMachine(hooks)
        self.reserved = 1 if cfg["reserve_control_worker"] else 0
        self.workers = max(1, min(cfg["max_parallel"], cfg["pool_size"] * self.PER_SLOT) - self.reserved)
        self.lease_timeout_ms = int(parse_duration(cfg["lease_timeout"]) * 1000)
        self.tick_ms = cfg["tick_ms"]
        self.legacy = bool(cfg["legacy_retry"])
        self.handlers: Dict[str, Handler] = {}
        self.running: List[str] = []
        self.delays_ms: List[int] = []

    # -- configuration --------------------------------------------------
    def register(self, kind: str, handler: Handler) -> None:
        self.handlers[kind] = handler

    def policy(self, kind: str) -> RetryPolicy:
        policy = policy_for_kind(kind, self.settings)
        return legacy_from(policy) if self.legacy else policy

    def capacity(self, kind: str) -> int:
        """Slots available to ``kind``; the reserved slot is for control jobs."""
        if is_control_plane(kind):
            return self.workers + self.reserved
        return self.workers

    # -- lifecycle --------------------------------------------------------
    def _wake_due(self) -> None:
        now = self.clock.now_ms()
        for job in self.store.by_state("RETRY_WAIT"):
            if job.not_before_ms <= now:
                self.machine.apply(job, "wake", self.policy(job.kind))
                self.store.put(job)

    def _on_failure(self, job, exc: BaseException) -> None:
        policy = self.policy(job.kind)
        self.machine.apply(job, "fail", policy)
        if job.state == "RETRY_WAIT":
            delay = policy.delay_ms(job.attempts)
            self.delays_ms.append(delay)
            job.not_before_ms = self.clock.now_ms() + delay
        job.meta["last_error"] = repr(exc)
        log.info("job %s failed (%s), now %s", job.id, exc, job.state)

    def _run(self, job) -> None:
        policy = self.policy(job.kind)
        handler = self.handlers.get(job.kind)
        self.machine.apply(job, "lease", policy)
        self.machine.apply(job, "start", policy)
        try:
            if handler is None:
                raise LookupError(f"no handler for kind {job.kind!r}")
            handler(job)
        except Exception as exc:  # handlers are user code
            self._on_failure(job, exc)
        else:
            self.machine.apply(job, "complete", policy)
        self.store.put(job)

    def tick(self) -> int:
        """One scheduling round. Returns the number of jobs run."""
        self._wake_due()
        ran = 0
        for job in self.store.by_state("QUEUED"):
            if ran >= self.capacity(job.kind):
                break
            self._run(job)
            ran += 1
        return ran

    def run_until_idle(self, max_ticks: int = 10000) -> int:
        ticks = 0
        while ticks < max_ticks:
            ticks += 1
            if self.tick():
                continue
            waiting = self.store.by_state("RETRY_WAIT")
            if not waiting:
                break
            due = min(j.not_before_ms for j in waiting)
            self.clock.sleep_ms(max(due - self.clock.now_ms(), self.tick_ms))
        return ticks
