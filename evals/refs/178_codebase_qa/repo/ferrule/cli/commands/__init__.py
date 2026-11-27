"""Command groups. Each module exposes ``group``."""

from __future__ import annotations

from typing import Dict


def overrides(ns) -> Dict[str, str]:
    out = {}
    for item in getattr(ns, "set", []) or []:
        key, _, value = item.partition("=")
        out[key.strip()] = value
    return out


def app_from(ns):
    from ferrule.app import build_app
    from ferrule.config import load_settings
    settings = load_settings(profile=getattr(ns, "profile", None), overrides=overrides(ns))
    return build_app(settings)
