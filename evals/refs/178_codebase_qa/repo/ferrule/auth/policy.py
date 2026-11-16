"""Permission evaluation.

``Policy.can(role, action, owner=...)`` walks the role and its ancestors
breadth first. The *nearest* level that has any rule matching the action
decides: if one of that level's matching rules is a deny, the answer is
no, otherwise yes. A role reachable along several inheritance paths sits at
its nearest depth. If no level has a matching rule, the answer is no.
"""

from __future__ import annotations

import copy
import logging
from fnmatch import fnmatchcase
from typing import Dict, Iterable, List

from ferrule.auth.roles import BUILTIN_ROLES, RULE_FIELDS
from ferrule.config.ini import split_list
from ferrule.errors import ConfigError

log = logging.getLogger(__name__)

OWN = ":own"


def _as_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return split_list(str(value))


def _matches(rule: str, action: str, owner: bool) -> bool:
    if rule.endswith(OWN):
        if not owner:
            return False
        rule = rule[: -len(OWN)]
    return fnmatchcase(action, rule)


class Policy:
    def __init__(self, roles: Dict[str, Dict[str, List[str]]]) -> None:
        self.roles = roles
        for name, spec in roles.items():
            for parent in spec.get("inherits", []):
                if parent not in roles:
                    raise ConfigError(f"role {name} inherits unknown role {parent}")

    @classmethod
    def from_settings(cls, settings) -> "Policy":
        roles = copy.deepcopy(BUILTIN_ROLES)
        overlays = settings["auth"].get("roles") or {}
        for name, spec in overlays.items():
            base = roles.get(name, {f: [] for f in RULE_FIELDS})
            merged = dict(base)
            for key, value in spec.items():
                if key not in RULE_FIELDS:
                    raise ConfigError(f"auth.roles.{name}: unknown field {key!r}")
                merged[key] = _as_list(value)
            roles[name] = merged
        return cls(roles)

    def names(self) -> List[str]:
        return sorted(self.roles)

    def _levels(self, role: str) -> Iterable[List[str]]:
        if role not in self.roles:
            return
        seen = {role}
        level = [role]
        while level:
            yield level
            nxt: List[str] = []
            for name in level:
                for parent in self.roles[name].get("inherits", []):
                    if parent not in seen:
                        seen.add(parent)
                        nxt.append(parent)
            level = nxt

    def can(self, role: str, action: str, *, owner: bool = False) -> bool:
        for level in self._levels(role):
            allowed = denied = False
            for name in level:
                spec = self.roles[name]
                if any(_matches(r, action, owner) for r in spec.get("deny", [])):
                    denied = True
                if any(_matches(r, action, owner) for r in spec.get("allow", [])):
                    allowed = True
            if denied:
                return False
            if allowed:
                return True
        return False

    def explain(self, role: str, action: str, *, owner: bool = False) -> str:
        for depth, level in enumerate(self._levels(role)):
            for name in level:
                spec = self.roles[name]
                for kind in ("deny", "allow"):
                    for rule in spec.get(kind, []):
                        if _matches(rule, action, owner):
                            return f"{kind} by {name}:{rule} at depth {depth}"
        return "no matching rule"
