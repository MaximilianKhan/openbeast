"""Derive tags from the job kind and payload."""

from __future__ import annotations

from ferrule.constants import MAX_TAGS, PRIORITY_NORMAL


def apply_tags(job, ctx) -> None:
    family = job.kind.split(".", 1)[0]
    tags = set(job.tags)
    tags.add(f"family:{family}")
    if job.payload.get("priority") == "high":
        tags.add("hot")
    job.tags = sorted(tags)[:MAX_TAGS]


def normalize_tags(job, ctx) -> None:
    job.tags = sorted({t.strip().lower() for t in job.tags if t.strip()})


def setup(hooks, settings) -> None:
    hooks.hook("before_submit")(apply_tags)
    hooks.register("before_submitted", normalize_tags)
    # Tags must exist before the SLA plugin reads them.
    hooks.register("before_submit", apply_tags, priority=PRIORITY_NORMAL + 20)
