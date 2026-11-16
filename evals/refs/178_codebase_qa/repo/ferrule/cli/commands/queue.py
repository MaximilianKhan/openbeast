"""``ferrule queue ...``"""

from __future__ import annotations

from collections import Counter

from ferrule.cli.commands import app_from
from ferrule.cli.dispatch import Group, arg
from ferrule.jobs.service import Context
from ferrule.util.text import table

group = Group("queue", aliases=("queues",), help="inspect and control queues", default="list")


@group.command("list", aliases=("ls",))
@arg("--all", action="store_true", help="include empty queues")
@arg("--tenant", default="default")
def list_queues(ns) -> int:
    """List queues with their job counts."""
    app = app_from(ns)
    jobs = app.service.list(Context(tenant=ns.tenant), limit=10**6)
    counts = Counter(j.queue for j in jobs)
    names = sorted(set(counts) | ({"default"} if ns.all else set()))
    print(table([(n, counts.get(n, 0), "paused" if n in app.paused else "") for n in names],
                ["queue", "jobs", ""]))
    return 0


@group.command("pause")
@arg("name")
def pause(ns) -> int:
    """Stop leasing jobs from a queue."""
    app = app_from(ns)
    app.paused.add(ns.name)
    print(f"paused {ns.name}")
    return 0


@group.command("resume")
@arg("name")
def resume(ns) -> int:
    """Resume a paused queue."""
    app = app_from(ns)
    app.paused.discard(ns.name)
    print(f"resumed {ns.name}")
    return 0


@group.command("drain")
@arg("name")
@arg("--tenant", default="default")
def drain(ns) -> int:
    """Cancel every queued job in a queue."""
    app = app_from(ns)
    ctx = Context(actor="cli", role="admin", tenant=ns.tenant)
    n = 0
    for job in app.service.list(ctx, state="QUEUED", limit=10**6):
        if job.queue == ns.name:
            app.service.cancel(job.id, ctx)
            n += 1
    print(f"cancelled {n} job(s)")
    return 0
