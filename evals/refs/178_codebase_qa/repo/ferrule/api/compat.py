"""Pre-2.0 routes, kept behind ``api.legacy_routes``.

Old clients called actions with a colon suffix (``POST /jobs/{id}:retry``)
and listed jobs under ``/job``.
"""

from __future__ import annotations

from ferrule.api.routing import Router
from ferrule.api.views.jobs import JobsView


def install(root: Router) -> None:
    legacy = Router("/")
    legacy.add("POST", "/jobs/{job_id}:retry", JobsView.retry, "jobs.retry")
    legacy.add("POST", "/jobs/{job_id}:cancel", JobsView.cancel, "jobs.cancel")
    legacy.add("GET", "/job", JobsView.index, "jobs.index")
    root.include(legacy)
