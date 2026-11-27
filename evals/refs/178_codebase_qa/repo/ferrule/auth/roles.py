"""Built-in roles.

A rule is an action pattern (``fnmatch`` syntax, so ``jobs.*`` and ``*``
work). A rule ending in ``:own`` applies only when the caller owns the
object. Roles inherit the rules of the roles they list in ``inherits``.

Deployments can add roles or change these ones under ``[auth.roles.<name>]``
in the config; see ``Policy.from_settings``.
"""

from __future__ import annotations

from typing import Dict, List

BUILTIN_ROLES: Dict[str, Dict[str, List[str]]] = {
    "viewer": {
        "inherits": [],
        "allow": ["jobs.read", "jobs.list", "queues.read"],
        "deny": [],
    },
    "operator": {
        "inherits": ["viewer"],
        "allow": ["jobs.retry", "jobs.cancel:own", "jobs.create"],
        "deny": [],
    },
    "maintainer": {
        "inherits": ["operator"],
        "allow": ["jobs.*", "queues.*"],
        "deny": ["jobs.purge"],
    },
    "lead": {
        "inherits": ["maintainer"],
        "allow": ["tenants.read"],
        # Leads cancel other people's work, not their own; theirs goes
        # through review.
        "deny": ["jobs.cancel:own"],
    },
    "auditor": {
        "inherits": ["viewer"],
        "allow": ["audit.*"],
        "deny": ["jobs.*"],
    },
    "support": {
        "inherits": ["operator", "auditor"],
        "allow": ["jobs.cancel", "tenants.read"],
        "deny": [],
    },
    "sre": {
        "inherits": ["maintainer", "auditor"],
        "allow": [],
        "deny": [],
    },
    "release": {
        "inherits": ["sre"],
        "allow": ["jobs.cancel:own", "queues.drain"],
        "deny": [],
    },
    "billing": {
        "inherits": ["viewer"],
        "allow": ["invoices.*", "tenants.read"],
        "deny": [],
    },
    "admin": {
        "inherits": ["maintainer"],
        "allow": ["*"],
        "deny": [],
    },
}

RULE_FIELDS = ("inherits", "allow", "deny")
