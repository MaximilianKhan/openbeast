"""Liveness and readiness. Mounted outside the versioned prefix."""

from __future__ import annotations

from ferrule.api.routing import Router

health_router = Router()


@health_router.route("/healthz")
def healthz(app, request):
    return 200, {"ok": True}


@health_router.route("/readyz")
def readyz(app, request):
    try:
        app.store.count()
    except Exception as exc:  # noqa: BLE001 - report, don't crash the probe
        return 503, {"ok": False, "error": repr(exc)}
    return 200, {"ok": True}
