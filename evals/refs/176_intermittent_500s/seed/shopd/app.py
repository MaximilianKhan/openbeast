"""Application wiring.

``create_app()`` builds every component once and returns an ``App``. One
App serves all worker threads: the database, the cache, the idempotency
store and the rate limiter are shared; nothing request-specific is kept on
the App between requests.
"""
from __future__ import annotations

from . import __version__
from . import cache as cachemod
from . import handlers, middleware
from .clock import SystemClock
from .config import Config
from .db import Database
from .idempotency import IdempotencyStore
from .log import Logger
from .router import Router
from .services.catalog import Catalog
from .services.inventory import Inventory
from .services.orders import OrderService
from .services.promotions import Promotions
from .services.reports import Reports


class Services:
    """Everything a handler may use, built once per App."""

    def __init__(self, config: Config, clock, logger):
        self.version = __version__
        self.config = config
        self.clock = clock
        self.logger = logger
        self.db = Database()
        self.cache = cachemod.TTLCache(clock, config.cache_ttl_ms, config.cache_max_entries)
        self.catalog = Catalog(self.db, self.cache, clock, logger)
        self.inventory = Inventory(self.db, self.catalog, logger)
        self.promotions = Promotions(self.db, logger)
        self.orders = OrderService(self.db, self.catalog, self.inventory, self.promotions,
                                   clock, config, logger)
        self.reports = Reports(self.db, self._tiers)
        self.idempotency = IdempotencyStore(clock, config.idempotency_ttl_ms)

    def _tiers(self) -> dict:
        return {row["id"]: row["tier"] for row in self.db.customers.scan()}


class App:
    def __init__(self, config: Config | None = None, clock=None, log_sink=None):
        self.config = config or Config()
        self.clock = clock or SystemClock()
        self.logger = Logger(self.clock, log_sink, default_worker=self.config.default_worker)
        self.services = Services(self.config, self.clock, self.logger)
        self.router = Router()
        handlers.register_all(self.router, self.services)
        self._pipeline = middleware.chain(
            [middleware.RequestIds(),
             middleware.AccessLog(self.logger, self.config),
             middleware.Errors(self.logger),
             middleware.RateLimit(self.clock, self.config)],
            self._dispatch,
        )

    def _dispatch(self, request):
        route, params = self.router.resolve(request.method, request.path)
        with self.services.db.lock:
            return middleware.ensure_response(route.handler(request, params))

    def handle(self, request):
        """Serve one request. Never raises."""
        return self._pipeline(request)

    # Convenience for tests and scripts -----------------------------------
    def call(self, method: str, target: str, json_body=None, headers=None, client: str = "test"):
        from .http import Request
        return self.handle(Request.build(method, target, headers=headers, json_body=json_body,
                                         client=client))


def create_app(config: Config | None = None, clock=None, log_sink=None) -> App:
    return App(config=config, clock=clock, log_sink=log_sink)
