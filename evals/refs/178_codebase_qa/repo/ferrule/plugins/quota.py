"""Per-tenant submission quota over a sliding window."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, DefaultDict

from ferrule.errors import QuotaExceeded
from ferrule.util.durations import parse_duration

_WINDOWS: DefaultDict[str, Deque[float]] = defaultdict(deque)
_STATE = {"limit": 1000, "window_s": 3600.0, "overrides": {}}


def _limit_for(tenant: str) -> int:
    return int(_STATE["overrides"].get(tenant, _STATE["limit"]))


def enforce(job, ctx) -> None:
    now = time.monotonic()
    q = _WINDOWS[job.tenant]
    while q and now - q[0] > _STATE["window_s"]:
        q.popleft()
    if len(q) >= _limit_for(job.tenant):
        raise QuotaExceeded(f"tenant {job.tenant} is over its quota")
    q.append(now)


def usage(tenant: str) -> int:
    return len(_WINDOWS.get(tenant, ()))


def reset(tenant: str) -> None:
    _WINDOWS.pop(tenant, None)


def setup(hooks, settings) -> None:
    cfg = settings["quota"]
    _STATE["limit"] = cfg["default_limit"]
    _STATE["window_s"] = parse_duration(cfg["window"])
    _STATE["overrides"] = dict(cfg.get("tenants") or {})
    if cfg["enforce"]:
        hooks.register("before-submit", enforce, priority=80)
