"""A small router.

Routes are path templates (``/jobs/{job_id}``). Routers nest: a router has
its own prefix, and ``include(child, prefix)`` mounts a child router below
it, so the full path of a route is the concatenation of every prefix on
the way down plus the route's own path.

Class-based views group the actions of one resource; see ``View``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

_PARAM = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def join(*parts: str) -> str:
    """Join path pieces with single slashes; never a trailing slash."""
    pieces = [p.strip("/") for p in parts if p and p.strip("/")]
    return "/" + "/".join(pieces)


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    handler: Callable[..., Any]
    name: str

    def compile(self) -> "re.Pattern[str]":
        pattern = _PARAM.sub(r"(?P<\1>[^/]+)", re.escape(self.path).replace(r"\{", "{").replace(r"\}", "}"))
        return re.compile("^" + pattern + "$")


# -- class-based views ------------------------------------------------------

_ACTION_ATTR = "__ferrule_actions__"


def action(path: str = "", methods: Sequence[str] = ("POST",), name: Optional[str] = None):
    """Mark a View method as an endpoint. Actions default to POST."""
    def deco(fn):
        actions = getattr(fn, _ACTION_ATTR, [])
        actions.append((path, tuple(m.upper() for m in methods), name))
        setattr(fn, _ACTION_ATTR, actions)
        return fn
    return deco


def get(path: str = "", name: Optional[str] = None):
    return action(path, methods=("GET",), name=name)


class View:
    """Group of endpoints for one resource.

    ``resource`` is used for default route names: ``<resource>.<method>``.
    """

    resource = "resource"

    def __init__(self, app) -> None:
        self.app = app

    @classmethod
    def endpoints(cls) -> Iterator[Tuple[str, Tuple[str, ...], str, str]]:
        for attr in sorted(vars(cls)):
            fn = vars(cls)[attr]
            for path, methods, name in getattr(fn, _ACTION_ATTR, []):
                yield path, methods, name or f"{cls.resource}.{attr}", attr


class Router:
    def __init__(self, prefix: str = "") -> None:
        self.prefix = prefix
        self._routes: List[Tuple[str, str, Any, str]] = []
        self._views: List[type] = []
        self._children: List[Tuple[str, "Router"]] = []

    def route(self, path: str, methods: Sequence[str] = ("GET",), name: Optional[str] = None):
        def deco(fn):
            for m in methods:
                self._routes.append((m.upper(), path, fn, name or fn.__name__))
            return fn
        return deco

    def add(self, method: str, path: str, handler: Any, name: str) -> None:
        self._routes.append((method.upper(), path, handler, name))

    def mount_view(self, view_cls: type) -> type:
        self._views.append(view_cls)
        return view_cls

    def include(self, child: "Router", prefix: str = "") -> None:
        self._children.append((prefix, child))

    def iter_routes(self, base: str = "") -> Iterator[Route]:
        here = join(base, self.prefix)
        for method, path, handler, name in self._routes:
            yield Route(method, join(here, path), handler, name)
        for view_cls in self._views:
            for path, methods, name, attr in view_cls.endpoints():
                for m in methods:
                    yield Route(m, join(here, path), getattr(view_cls, attr), name)
        for prefix, child in self._children:
            yield from child.iter_routes(join(here, prefix))

    def routes(self) -> List[Route]:
        return list(self.iter_routes())


class RouteTable:
    """Compiled routes for dispatch."""

    def __init__(self, router: Router, trailing_slash: bool = False) -> None:
        self.trailing_slash = trailing_slash
        self.routes = router.routes()
        self._compiled = [(r, r.compile()) for r in self.routes]

    def match(self, method: str, path: str) -> Tuple[Optional[Route], Dict[str, str]]:
        if not self.trailing_slash and len(path) > 1:
            path = path.rstrip("/")
        for route, rx in self._compiled:
            if route.method != method.upper():
                continue
            m = rx.match(path)
            if m:
                return route, m.groupdict()
        return None, {}

    def names(self) -> List[str]:
        return sorted({r.name for r in self.routes})
