"""HTTP API. ``build_router(settings)`` returns the root router."""

from __future__ import annotations

from ferrule.api import compat
from ferrule.api.routing import Router, join
from ferrule.api.views.admin import admin_router
from ferrule.api.views.health import health_router
from ferrule.api.views.jobs import jobs_router
from ferrule.api.views.queues import queues_router
from ferrule.api.views.tenants import tenants_router


def build_router(settings) -> Router:
    api = settings["api"]
    root = Router(join(api["prefix"], api["version"]))
    root.include(jobs_router)
    root.include(queues_router)
    root.include(tenants_router)
    root.include(admin_router)
    if api["legacy_routes"]:
        compat.install(root)
    top = Router()
    top.include(health_router)
    top.include(root)
    return top
