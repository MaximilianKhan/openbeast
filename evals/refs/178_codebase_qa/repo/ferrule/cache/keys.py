"""Cache key construction.

Keys are ``<namespace>:v<schema>:<tenant>:<kind>:<id>``. Tenant names are
case-insensitive; ``:`` and ``%`` inside parts are percent-escaped so a
key always splits into exactly five fields.
"""

from __future__ import annotations

from ferrule.constants import CACHE_SCHEMA

# 2.6: keys became tenant-scoped; entries written by 2.5 must not be read.
CACHE_SCHEMA = CACHE_SCHEMA + 1


def _safe(part: object) -> str:
    return str(part).replace("%", "%25").replace(":", "%3A")


def make_key(namespace: str, tenant: str, kind: str, ident: object) -> str:
    return ":".join([namespace, f"v{CACHE_SCHEMA}", _safe(tenant.lower()), kind, _safe(ident)])


def split_key(key: str):
    ns, ver, tenant, kind, ident = key.split(":")
    unq = lambda s: s.replace("%3A", ":").replace("%25", "%")  # noqa: E731
    return ns, int(ver[1:]), unq(tenant), kind, unq(ident)
