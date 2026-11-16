"""Job lifecycle.

::

    NEW --submit--> QUEUED --lease--> LEASED --start--> RUNNING --complete--> SUCCEEDED
                      ^                 |                  |
                      |              expire              fail / expire
                      |                 v                  v
                      +---------------QUEUED          RETRY_WAIT --wake--> QUEUED
                                                         |
                                            (attempts exhausted) -> DEAD

Every failure increments ``attempts``. A lease expiry counts as a failure
only when the policy has ``count_expiry``. When a counted failure exhausts
the policy, the job goes to DEAD instead of RETRY_WAIT / QUEUED.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Dict, Optional, Tuple

from ferrule.errors import InvalidTransition
from ferrule.jobs.retry import RetryPolicy

log = logging.getLogger(__name__)


class State(str, Enum):
    NEW = "NEW"
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    DEAD = "DEAD"
    CANCELLED = "CANCELLED"


TRANSITIONS: Dict[Tuple[str, str], str] = {
    ("NEW", "submit"): "QUEUED",
    ("QUEUED", "lease"): "LEASED",
    ("QUEUED", "cancel"): "CANCELLED",
    ("LEASED", "start"): "RUNNING",
    ("LEASED", "expire"): "QUEUED",
    ("LEASED", "cancel"): "CANCELLED",
    ("RUNNING", "complete"): "SUCCEEDED",
    ("RUNNING", "fail"): "RETRY_WAIT",
    ("RUNNING", "expire"): "RETRY_WAIT",
    ("RETRY_WAIT", "wake"): "QUEUED",
    ("RETRY_WAIT", "cancel"): "CANCELLED",
    ("DEAD", "resurrect"): "QUEUED",
}

_FAILURES = {"fail"}
_EXPIRIES = {"expire"}


class JobStateMachine:
    def __init__(self, hooks=None, strict: bool = False) -> None:
        self.hooks = hooks
        self.strict = strict

    def _counts(self, event: str, policy: RetryPolicy) -> bool:
        if event in _FAILURES:
            return True
        return event in _EXPIRIES and policy.count_expiry

    def apply(self, job, event: str, policy: RetryPolicy) -> Optional[str]:
        """Apply ``event`` to ``job``. Returns the new state.

        An event with no transition from the current state raises
        ``InvalidTransition`` when the machine is strict; otherwise it is
        recorded in the history and ignored (returns ``None``).
        """
        old = job.state
        new = TRANSITIONS.get((old, event))
        if new is None:
            if self.strict:
                raise InvalidTransition(old, event)
            job.history.append((old, event, "ignored"))
            log.debug("job %s: ignored %s in %s", job.id, event, old)
            return None
        if self._counts(event, policy):
            job.attempts += 1
            if policy.exhausted(job.attempts):
                new = "DEAD"
        if event == "resurrect":
            job.attempts = 0
        job.state = new
        job.history.append((old, event, new))
        if self.hooks is not None:
            self.hooks.fire("after_transition", job, old, new)
        return new
