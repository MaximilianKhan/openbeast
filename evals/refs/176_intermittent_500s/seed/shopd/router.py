"""Path routing.

Patterns are literal segments plus ``{name}`` placeholders, e.g.
``/orders/{id}/cancel``. A placeholder matches one non-empty path segment and
is passed to the handler as a string in ``params``; handlers convert and
validate it themselves.
"""
from __future__ import annotations

import re

from .errors import MethodNotAllowed, NotFound

_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class Route:
    def __init__(self, method: str, pattern: str, handler, name: str):
        self.method = method
        self.pattern = pattern
        self.handler = handler
        self.name = name
        parts = []
        pos = 0
        for m in _PLACEHOLDER.finditer(pattern):
            parts.append(re.escape(pattern[pos:m.start()]))
            parts.append("(?P<%s>[^/]+)" % m.group(1))
            pos = m.end()
        parts.append(re.escape(pattern[pos:]))
        self.regex = re.compile("^" + "".join(parts) + "$")

    def match(self, path: str):
        m = self.regex.match(path)
        return None if m is None else m.groupdict()


class Router:
    def __init__(self):
        self.routes: list[Route] = []

    def add(self, method: str, pattern: str, handler, name: str | None = None) -> None:
        self.routes.append(Route(method.upper(), pattern, handler, name or handler.__name__))

    def resolve(self, method: str, path: str):
        """Return ``(route, params)`` or raise NotFound / MethodNotAllowed."""
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        allowed = []
        for route in self.routes:
            params = route.match(path)
            if params is None:
                continue
            if route.method == method:
                return route, params
            allowed.append(route.method)
        if allowed:
            err = MethodNotAllowed("method %s not allowed here" % method)
            err.headers["Allow"] = ", ".join(sorted(set(allowed)))
            raise err
        raise NotFound("no route for %s" % path, code="route_not_found")
