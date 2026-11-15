"""Entry point: ``python -m ferrule <group> <command> ...``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List, Optional

from ferrule.cli.commands import admin, config, job, queue, quota
from ferrule.cli.dispatch import Registry
from ferrule.errors import FerruleError

REGISTRY = Registry()
for _module in (queue, quota, job, admin, config):
    REGISTRY.add(_module.group)

# 1.x muscle memory. Only applied with FERRULE_CLI_COMPAT=1 because "q"
# became the quota alias in 2.3.
_LEGACY_SHORTCUTS = {"q": "queue", "j": "job", "ls": "queue"}


def _expand_legacy(argv: List[str]) -> List[str]:
    if os.environ.get("FERRULE_CLI_COMPAT") == "1" and argv and argv[0] in _LEGACY_SHORTCUTS:
        return [_LEGACY_SHORTCUTS[argv[0]]] + argv[1:]
    return argv


def main(argv: Optional[List[str]] = None) -> int:
    argv = _expand_legacy(list(sys.argv[1:] if argv is None else argv))
    logging.basicConfig(level=os.environ.get("FERRULE_LOG", "WARNING"))
    try:
        group, cmd, rest = REGISTRY.lookup(argv)
    except FerruleError as exc:
        print(f"ferrule: {exc}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog=f"ferrule {group.name} {cmd.name}", description=cmd.help)
    parser.add_argument("--profile")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    for flags, kwargs in cmd.args:
        parser.add_argument(*flags, **kwargs)
    ns = parser.parse_args(rest)
    try:
        return int(cmd.func(ns) or 0)
    except FerruleError as exc:
        print(f"ferrule: {exc}", file=sys.stderr)
        return 1
