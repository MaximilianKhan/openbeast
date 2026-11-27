"""Stamp a completion deadline on every job."""

from __future__ import annotations

import time

from ferrule.util.durations import parse_duration

_STATE = {"deadline_s": 900.0}


def stamp_deadline(job, ctx) -> None:
    if "deadline" not in job.meta:
        hot = "hot" in job.tags
        job.meta["deadline"] = time.time() + _STATE["deadline_s"] / (2 if hot else 1)


def check_deadline(job, old, new) -> None:
    deadline = job.meta.get("deadline")
    if deadline and new == "SUCCEEDED" and time.time() > deadline:
        job.meta["sla_missed"] = True


def setup(hooks, settings) -> None:
    cfg = settings["sla"]
    _STATE["deadline_s"] = parse_duration(cfg["default_deadline"])
    strict = settings["plugins"].get("sla_strict")
    hooks.register("before_submit", stamp_deadline, priority=85 if strict else 80)
    hooks.register("after_transition", check_deadline)
