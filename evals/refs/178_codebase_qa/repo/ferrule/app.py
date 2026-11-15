"""Application wiring.

``build_app()`` is the composition root: it loads settings, builds the
store, registry, policy and services, loads plugins and builds the router.
Everything else receives its collaborators from here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Set

from ferrule import metrics
from ferrule.api import build_router
from ferrule.api.routing import RouteTable
from ferrule.api.server import ApiServer
from ferrule.auth.policy import Policy
from ferrule.auth.tokens import TokenStore
from ferrule.config import Settings, load_settings
from ferrule.events import EventBus
from ferrule.hooks import HookRegistry
from ferrule.jobs.clock import Clock
from ferrule.jobs.scheduler import Scheduler
from ferrule.jobs.service import JobService
from ferrule.plugins import load_plugins
from ferrule.ratelimit import RateLimiter
from ferrule.storage import create_store

log = logging.getLogger(__name__)


@dataclass
class App:
    settings: Settings
    store: Any
    hooks: HookRegistry
    policy: Policy
    events: EventBus
    service: JobService
    scheduler: Scheduler
    tokens: TokenStore
    limiter: RateLimiter
    router: Any = None
    routes: Optional[RouteTable] = None
    server: Optional[ApiServer] = None
    plugins: list = field(default_factory=list)
    paused: Set[str] = field(default_factory=set)


def build_app(settings: Optional[Settings] = None, *, env: Optional[Mapping[str, str]] = None,
              overrides: Optional[Mapping[str, Any]] = None, clock: Optional[Clock] = None) -> App:
    settings = settings or load_settings(env=env, overrides=overrides)
    hooks = HookRegistry()
    store = create_store(settings)
    policy = Policy.from_settings(settings)
    events = EventBus(settings)
    metrics.attach(events)
    service = JobService(settings, store, hooks, policy, events)
    scheduler = Scheduler(settings, store, hooks, clock=clock)
    app = App(settings=settings, store=store, hooks=hooks, policy=policy, events=events,
              service=service, scheduler=scheduler, tokens=TokenStore(settings),
              limiter=RateLimiter(settings, clock=clock))
    app.plugins = load_plugins(hooks, settings)
    app.router = build_router(settings)
    app.routes = RouteTable(app.router, trailing_slash=settings["api"]["trailing_slash"])
    app.server = ApiServer(app, app.routes)
    log.info("ferrule ready: profile=%s store=%s workers=%d plugins=%s",
             settings["profile"], store.name, scheduler.workers, ",".join(app.plugins))
    return app
