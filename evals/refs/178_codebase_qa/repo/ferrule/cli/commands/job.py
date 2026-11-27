"""``ferrule job ...``"""

from __future__ import annotations

import json

from ferrule.cli.commands import app_from
from ferrule.cli.dispatch import Group, arg
from ferrule.jobs.service import Context
from ferrule.util.ids import short
from ferrule.util.text import table

group = Group("job", aliases=("jobs", "j"), help="submit and inspect jobs", default="list")


@group.command("list", aliases=("ls",))
@arg("--state")
@arg("--tenant", default="default")
@arg("--limit", type=int, default=50)
def list_jobs(ns) -> int:
    """List jobs."""
    app = app_from(ns)
    jobs = app.service.list(Context(tenant=ns.tenant), state=ns.state, limit=ns.limit)
    print(table([(short(j.id), j.kind, j.state, j.attempts) for j in jobs],
                ["id", "kind", "state", "attempts"]))
    return 0


@group.command("submit", aliases=("add",))
@arg("kind")
@arg("--payload", default="{}")
@arg("--tenant", default="default")
@arg("--dry-run", action="store_true")
def submit(ns) -> int:
    """Submit a job."""
    app = app_from(ns)
    ctx = Context(actor="cli", role="operator", tenant=ns.tenant, dry_run=ns.dry_run)
    job = app.service.submit(ns.kind, json.loads(ns.payload), ctx)
    print(job.id)
    return 0


@group.command("show")
@arg("job_id")
@arg("--tenant", default="default")
def show(ns) -> int:
    """Show one job as JSON."""
    app = app_from(ns)
    print(json.dumps(app.service.get(ns.job_id, Context(tenant=ns.tenant)).to_dict(), indent=2))
    return 0


@group.command("cancel")
@arg("job_id")
@arg("--tenant", default="default")
@arg("--role", default="operator")
def cancel(ns) -> int:
    """Cancel a job."""
    app = app_from(ns)
    app.service.cancel(ns.job_id, Context(actor="cli", role=ns.role, tenant=ns.tenant))
    return 0


@group.command("retry")
@arg("job_id")
@arg("--tenant", default="default")
def retry(ns) -> int:
    """Re-queue a DEAD job with a fresh attempt budget."""
    app = app_from(ns)
    app.service.retry(ns.job_id, Context(actor="cli", role="operator", tenant=ns.tenant))
    return 0
