#!/usr/bin/env python3
"""Smoke test against a dev-profile app: submit, run, inspect.

    FERRULE_PROFILE=dev python scripts/smoke.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ferrule.app import build_app  # noqa: E402
from ferrule.jobs.clock import ManualClock  # noqa: E402
from ferrule.jobs.service import Context  # noqa: E402


def main() -> int:
    if os.environ.get("FERRULE_PROFILE") != "dev":
        print("refusing to run outside the dev profile (set FERRULE_PROFILE=dev)")
        return 2
    app = build_app(clock=ManualClock())
    ctx = Context(actor="smoke", role="operator")
    ok = app.service.submit("report.csv", {"rows": 3}, ctx)
    bad = app.service.submit("export.gcs", {"bucket": "x"}, ctx)
    app.scheduler.register("report.csv", lambda job: None)

    def fail(job):
        raise RuntimeError("bucket missing")

    app.scheduler.register("export.gcs", fail)
    ticks = app.scheduler.run_until_idle()
    for job in (app.store.get(ok.id), app.store.get(bad.id)):
        print(f"{job.kind:12s} {job.state:10s} attempts={job.attempts}")
    print(f"ticks={ticks} delays_ms={app.scheduler.delays_ms}")
    print("hooks:", app.hooks.callbacks("before_submit"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
