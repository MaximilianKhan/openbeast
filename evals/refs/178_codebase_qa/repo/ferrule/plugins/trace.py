"""Debug tracing of every hook a job passes through."""

from __future__ import annotations

import logging

log = logging.getLogger("ferrule.trace")


def trace_submit(job, ctx) -> None:
    log.debug("submit %s kind=%s tenant=%s", job.id, job.kind, job.tenant)


def trace_transition(job, old, new) -> None:
    log.debug("transition %s %s -> %s", job.id, old, new)


def setup(hooks, settings) -> None:
    hooks.register("before_submit", trace_submit, priority=1000)
    hooks.register("after_transition", trace_transition, priority=1000)
