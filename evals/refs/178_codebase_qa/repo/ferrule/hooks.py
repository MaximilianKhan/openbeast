"""Synchronous hook registry used by the job service and the plugins.

Hook names are normalised, so ``"before-submit"``, ``"BeforeSubmit"`` and
``"before_submit"`` all name the same hook. Callbacks run in descending
priority; callbacks with equal priority run in the order they were
registered. A callback can return ``STOP`` to end the chain early.

Registration is idempotent per callback *name*: registering a second
callback under a name that the hook already has is a no-op. This lets a
plugin's ``setup()`` be called more than once (tests, hot reload) without
doubling its callbacks.
"""

from __future__ import annotations

import itertools
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, DefaultDict, Dict, List, Optional

from ferrule.constants import PRIORITY_NORMAL

log = logging.getLogger(__name__)

STOP = object()

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def normalize(name: str) -> str:
    name = _CAMEL.sub("_", name.strip())
    return name.lower().replace("-", "_").replace(".", "_")


@dataclass(order=False)
class Callback:
    name: str
    fn: Callable[..., Any]
    priority: int
    seq: int
    tags: Dict[str, str] = field(default_factory=dict)


def _default_name(fn: Callable[..., Any]) -> str:
    module = getattr(fn, "__module__", "") or ""
    short = module.rsplit(".", 1)[-1]
    return f"{short}.{fn.__name__}"


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: DefaultDict[str, List[Callback]] = defaultdict(list)
        self._seq = itertools.count()
        self.fired: DefaultDict[str, int] = defaultdict(int)

    # -- registration -------------------------------------------------
    def register(self, hook: str, fn: Callable[..., Any], priority: int = PRIORITY_NORMAL,
                 name: Optional[str] = None, **tags: str) -> Callback:
        key = normalize(hook)
        cb_name = name or _default_name(fn)
        for existing in self._hooks[key]:
            if existing.name == cb_name:
                log.debug("hook %s: %s already registered", key, cb_name)
                return existing
        cb = Callback(cb_name, fn, int(priority), next(self._seq), dict(tags))
        self._hooks[key].append(cb)
        return cb

    def hook(self, hook: str, priority: int = PRIORITY_NORMAL,
             name: Optional[str] = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form of :meth:`register`."""
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.register(hook, fn, priority=priority, name=name)
            return fn
        return deco

    def unregister(self, hook: str, name: str) -> bool:
        key = normalize(hook)
        before = len(self._hooks[key])
        self._hooks[key] = [cb for cb in self._hooks[key] if cb.name != name]
        return len(self._hooks[key]) != before

    # -- introspection ------------------------------------------------
    def _ordered(self, hook: str) -> List[Callback]:
        return sorted(self._hooks.get(normalize(hook), []), key=lambda cb: (-cb.priority, cb.seq))

    def callbacks(self, hook: str) -> List[str]:
        """Names of the callbacks for ``hook``, in the order they run."""
        return [cb.name for cb in self._ordered(hook)]

    def hooks(self) -> List[str]:
        return sorted(k for k, v in self._hooks.items() if v)

    # -- dispatch -----------------------------------------------------
    def fire(self, hook: str, *args: Any, **kwargs: Any) -> List[Any]:
        key = normalize(hook)
        self.fired[key] += 1
        results = []
        for cb in self._ordered(key):
            out = cb.fn(*args, **kwargs)
            if out is STOP:
                log.debug("hook %s stopped by %s", key, cb.name)
                break
            results.append(out)
        return results
