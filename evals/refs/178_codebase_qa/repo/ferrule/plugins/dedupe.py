"""Reject a job that duplicates one submitted inside ``dedupe.window``.

The fingerprint is computed by the native extension over the canonical
JSON of the configured fields.
"""

from __future__ import annotations

import json
import time
from typing import Dict, Tuple

from ferrule import _native
from ferrule.constants import PRIORITY_EARLY
from ferrule.errors import DuplicateJob
from ferrule.util.durations import parse_duration

_SEEN: Dict[Tuple[str, str], float] = {}
_STATE = {"window_s": 600.0, "fields": ["kind", "tenant", "payload"]}


def fingerprint(job, ctx) -> None:
    doc = {f: getattr(job, f, None) for f in _STATE["fields"]}
    raw = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    job.meta["fingerprint"] = _native.fingerprint(raw)


def check(job, ctx) -> None:
    fp = job.meta.get("fingerprint")
    if not fp:
        return
    key = (job.tenant, fp)
    now = time.monotonic()
    seen = _SEEN.get(key)
    if seen is not None and now - seen < _STATE["window_s"]:
        raise DuplicateJob(f"duplicate of a job submitted {now - seen:.0f}s ago")
    _SEEN[key] = now


def setup(hooks, settings) -> None:
    cfg = settings["dedupe"]
    _STATE["window_s"] = parse_duration(cfg["window"])
    _STATE["fields"] = list(cfg["fields"])
    # fingerprint must run before check; same priority, so register it first.
    hooks.register("BeforeSubmit", fingerprint, priority=PRIORITY_EARLY)
    hooks.register("BeforeSubmit", check, priority=PRIORITY_EARLY)
