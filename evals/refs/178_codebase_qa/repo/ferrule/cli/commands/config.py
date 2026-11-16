"""``ferrule config ...``"""

from __future__ import annotations

import json

from ferrule.cli.commands import overrides
from ferrule.cli.dispatch import Group, arg
from ferrule.config import env_name, load_settings

group = Group("config", aliases=("cfg",), help="inspect configuration", default="show")


@group.command("show")
@arg("key", nargs="?")
def show(ns) -> int:
    """Print the effective settings (or one key)."""
    settings = load_settings(profile=ns.profile, overrides=overrides(ns))
    value = settings.path(ns.key) if ns.key else settings
    print(json.dumps(value, indent=2, sort_keys=True, default=str))
    return 0


@group.command("env")
@arg("key")
def env(ns) -> int:
    """Print the environment variable that overrides KEY."""
    print(env_name(ns.key))
    return 0
