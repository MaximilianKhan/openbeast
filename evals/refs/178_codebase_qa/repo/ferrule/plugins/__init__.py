"""Built-in plugins.

A plugin is a module in this package with a ``setup(hooks, settings)``
function. Which plugins load is controlled by ``plugins.enabled`` and
``plugins.disabled``; a name in both lists is not loaded.

``PLUGIN_ORDER`` is the canonical display order used by ``ferrule plugins
list`` and the docs.
"""

from __future__ import annotations

import importlib
import logging
from typing import Dict, List

from ferrule.hooks import HookRegistry

log = logging.getLogger(__name__)

PLUGIN_ORDER = ["trace", "sla", "audit", "dedupe", "quota", "tagger", "notify"]

_loaded: Dict[int, List[str]] = {}


def available() -> List[str]:
    return list(PLUGIN_ORDER)


def selected(settings) -> List[str]:
    cfg = settings["plugins"]
    disabled = set(cfg.get("disabled") or [])
    out = []
    for name in cfg.get("enabled") or []:
        if name in disabled:
            log.info("plugin %s is disabled", name)
            continue
        if name not in PLUGIN_ORDER:
            log.warning("unknown plugin %r ignored", name)
            continue
        if name not in out:
            out.append(name)
    return out


def load_plugins(hooks: HookRegistry, settings) -> List[str]:
    """Import and set up the selected plugins, in ``plugins.enabled`` order."""
    names = selected(settings)
    for name in names:
        module = importlib.import_module(f"ferrule.plugins.{name}")
        module.setup(hooks, settings)
    _loaded[id(hooks)] = names
    return names


def loaded(hooks: HookRegistry) -> List[str]:
    return list(_loaded.get(id(hooks), []))
