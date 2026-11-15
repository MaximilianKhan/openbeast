"""Send notifications for submitted and finished jobs."""

from __future__ import annotations

import json
import logging
import urllib.request

from ferrule.constants import PRIORITY_CORE

log = logging.getLogger("ferrule.notify")
_STATE = {"channels": ["log"], "timeout": 3.0, "webhook": None}


def _send(event: str, job) -> None:
    body = {"event": event, "job": job.id, "state": job.state}
    for channel in _STATE["channels"]:
        if channel == "log":
            log.info("notify %s", body)
        elif channel == "webhook" and _STATE["webhook"]:
            req = urllib.request.Request(_STATE["webhook"], data=json.dumps(body).encode(),
                                         headers={"content-type": "application/json"})
            urllib.request.urlopen(req, timeout=_STATE["timeout"])  # noqa: S310


def on_submit(job, ctx) -> None:
    _send("submitted", job)


def on_finish(job, old, new) -> None:
    if new in ("SUCCEEDED", "DEAD", "CANCELLED"):
        _send("finished", job)


def setup(hooks, settings) -> None:
    cfg = settings["notify"]
    _STATE["channels"] = list(cfg["channels"])
    _STATE["timeout"] = cfg["webhook_timeout_s"]
    _STATE["webhook"] = cfg.get("webhook_url")
    hooks.register("before_submit", on_submit, priority=PRIORITY_CORE)
    hooks.register("after_transition", on_finish)
