"""Audit trail: records who did what to which job.

Runs last on ``before_submit`` so the record reflects the job as it will be
stored (tags applied, deadline stamped).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from ferrule.constants import PRIORITY_LATE

log = logging.getLogger("ferrule.audit")

_TRAIL: List[Dict[str, Any]] = []


def record_submit(job, ctx) -> None:
    _TRAIL.append({
        "at": time.time(),
        "event": "submit",
        "job": job.id,
        "tenant": job.tenant,
        "actor": getattr(ctx, "actor", None),
    })


def record_transition(job, old: str, new: str) -> None:
    _TRAIL.append({"at": time.time(), "event": "transition", "job": job.id,
                   "from": old, "to": new})


def record_cancel(job, ctx) -> None:
    _TRAIL.append({"at": time.time(), "event": "cancel", "job": job.id,
                   "actor": getattr(ctx, "actor", None)})


def trail() -> List[Dict[str, Any]]:
    return list(_TRAIL)


def setup(hooks, settings) -> None:
    hooks.register("before_submit", record_submit, priority=PRIORITY_LATE)
    hooks.register("after_transition", record_transition, priority=PRIORITY_LATE)
    hooks.register("before_cancel", record_cancel, priority=PRIORITY_LATE)
