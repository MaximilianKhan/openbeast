#!/usr/bin/env python3
"""beast-hydra core: config, validation, health model and the routing decision.

Everything that decides WHERE a request goes lives here, and none of it does
network I/O. The same `decide()` answers a live request in agents/hydra.py,
a dry run on `/hydra/explain`, and `python3 agents/hydra.py --explain`, so a
routing question can be answered and tested without a single engine running
(docs/BEAST_HYDRA_PLAN.md §6.4).

The only I/O here is at config load: reading hydra.toml, stat()ing key files
(mode 0600, owned by us), and loading model profiles through
scripts/backends/pylib/obprofile.py. Key file CONTENTS are never read here.

Vocabulary (plan §0): a NODE is one engine endpoint; a DEPLOYMENT is one
model on one node and its id is strict; a ROUTE is a virtual model id with a
policy; RULES are deterministic when → then rewrites. The reconciliation
with beast-instinct adds one rule condition, `when.task_class`, fed only by
an `instinct-route/1` answer that says action=act AND enforce=true.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import random
import re
import stat
import sys
import time
import tomllib
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent
PYLIB = REPO / "scripts" / "backends" / "pylib"

SCHEMA = 1
ENGINES = ("llama", "vllm", "tensorfold", "openai")
CAPS = ("tools", "json_schema", "grammar", "vision", "reasoning_budget", "id_slot", "embeddings")
LLAMA_ONLY_CAPS = ("id_slot", "grammar")
NODE_ROLES = ("inference", "instinct-engine")

# Deployment health states (plan §6.5). Drain and conformance are orthogonal flags.
UNKNOWN, LOADING, READY, DOWN, AUTH_FAILED, MISMATCH = (
    "UNKNOWN", "LOADING", "READY", "DOWN", "AUTH_FAILED", "MISMATCH")
STATES = (UNKNOWN, LOADING, READY, DOWN, AUTH_FAILED, MISMATCH)
CLOSED, OPEN, HALF_OPEN = "CLOSED", "OPEN", "HALF_OPEN"

NODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
DEPLOYMENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]*(@[a-z0-9][a-z0-9_-]*)?$")
ROUTE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
HOURS_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")
# The SAME patterns scripts/lib/backend.sh and net.sh use. Parity is a test
# (tests/test_hydra_ready_parity.sh): the bash probe and this one must never
# disagree about whether an engine can serve.
_READY_STATUS_RE = re.compile(r'"status"[ \t\n\r\f\v]*:[ \t\n\r\f\v]*"ok"')
_READY_OK_TRUE_RE = re.compile(r'"ok"[ \t\n\r\f\v]*:[ \t\n\r\f\v]*true')
TAILNET = ipaddress.ip_network("100.64.0.0/10")


class ConfigError(ValueError):
    """Validation failed. `errors` is the full list (fail closed, report all)."""

    def __init__(self, errors: list[str], warnings: list[str] | None = None):
        super().__init__("; ".join(errors))
        self.errors = list(errors)
        self.warnings = list(warnings or [])


# ───────────────────────────────── config model ─────────────────────────────────

@dataclass(frozen=True)
class Breaker:
    fail_threshold: int = 5
    open_s: float = 30.0
    success_threshold: int = 2


@dataclass(frozen=True)
class InstinctCfg:
    """The instinct-route/1 client (reconciliation §1, §5). Never a pool."""
    enabled: bool = True
    url: str = "http://127.0.0.1:8094"
    key_file: str = ".run/instinct.key"
    deadline_ms: int = 25
    contract_ttl_s: float = 60.0
    feedback: bool = True
    engine_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    default_route: str = "beast"
    unknown_model: str = "default"
    inbound_key_env: str = "LLAMA_API_KEY"
    probe_interval_s: float = 5.0
    probe_down_interval_s: float = 30.0
    models_interval_s: float = 60.0
    down_after: int = 2
    up_after: int = 2
    pre_commit_budget_s: float = 580.0
    chars_per_token: float = 3.0
    ctx_margin: float = 0.05
    default_max_tokens: int = 4096
    list_deployments: bool = True
    affinity_ttl_s: float = 1800.0
    affinity_max: int = 4096
    audit: str = ".run/hydra-audit.jsonl"
    audit_max_mb: int = 50
    # The fleet-wide family policy (Max, 2026-09-30: "all of our models are
    # uncensored"). Non-empty = every NON-strict candidate — first choice,
    # spill, failover, ctx last resort, after any rule hop — must be one of
    # these families. Empty = no policy (the implicit single-node config).
    allowed_families: tuple[str, ...] = ()
    breaker: Breaker = Breaker()
    instinct: InstinctCfg = InstinctCfg()


@dataclass(frozen=True)
class Node:
    id: str
    url: str
    engine: str
    enabled: bool = True
    key_env: str | None = None
    key_file: str | None = None
    slots: int = 1
    # False only for the implicit node when INFERENCE_SLOTS is unset: `slots`
    # is then a routing guess (1), not the engine's real -np, and must not be
    # used to judge a caller's id_slot (forward_body).
    slots_known: bool = True
    connect_timeout_s: float = 5.0
    ttft_timeout_s: float = 120.0
    prefill_tps_floor: float | None = None
    idle_timeout_s: float = 120.0
    nonstream_timeout_s: float = 580.0
    gpu_lease: bool = False
    exclusive_group: str | None = None
    members: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    allow_public: bool = False
    role: str = "inference"
    loopback: bool = False

    @property
    def has_key(self) -> bool:
        return bool(self.key_env or self.key_file)


@dataclass(frozen=True)
class Deployment:
    id: str
    node: str
    upstream: str
    ctx: int
    family: str
    caps: frozenset
    conformance: str = "off"
    enabled: bool = True
    listed: bool = True
    profile: str | None = None
    # `verify_upstream = false` turns off the /v1/models MISMATCH check. Only
    # for a deployment whose served id is not known yet: the implicit config
    # (and the file --print-default-config writes from it) cannot know what
    # id a llama-server lists, and llama ignores the request's `model` anyway.
    verify_upstream: bool = True


@dataclass(frozen=True)
class Target:
    d: str
    priority: int = 0
    weight: float = 1.0


@dataclass(frozen=True)
class Route:
    id: str
    targets: tuple[Target, ...]
    aliases: tuple[str, ...] = ()
    spill: bool = True
    same_family: bool = False
    # The anchor family of a same_family route. Resolved at validation: the
    # explicit `family = "..."`, else the ONE family of the top-priority
    # targets (a tie across families is a config error — the anchor is never
    # inferred from list order). None when same_family is off.
    family: str | None = None
    affinity: str = "session"
    require: tuple[str, ...] = ()
    min_ctx: int = 0
    max_attempts: int = 3
    retry_on_ttft_timeout: bool = False
    description: str = ""
    listed: bool = True


@dataclass(frozen=True)
class Rule:
    name: str
    when: dict
    then: dict


@dataclass
class Config:
    settings: Settings
    nodes: dict[str, Node]
    deployments: dict[str, Deployment]
    routes: dict[str, Route]
    rules: list[Rule]
    warnings: list[str] = field(default_factory=list)
    source: str = "implicit"
    hash: str = ""
    route_by_id_or_alias: dict[str, Route] = field(default_factory=dict)

    @property
    def default_route(self) -> str:
        return self.settings.default_route

    def uses_task_class(self) -> bool:
        return any("task_class" in r.when for r in self.rules)


# ─────────────────────────────────── validation ──────────────────────────────────

_TOP_KEYS = {"schema", "hydra", "nodes", "deployments", "routes", "rules"}
_HYDRA_TYPES: dict[str, tuple] = {
    "default_route": (str,), "unknown_model": (str,), "inbound_key_env": (str,),
    "probe_interval_s": (int, float), "probe_down_interval_s": (int, float),
    "models_interval_s": (int, float), "down_after": (int,), "up_after": (int,),
    "pre_commit_budget_s": (int, float), "chars_per_token": (int, float),
    "ctx_margin": (int, float), "default_max_tokens": (int,), "list_deployments": (bool,),
    "affinity_ttl_s": (int, float), "affinity_max": (int,), "audit": (str,),
    "audit_max_mb": (int,), "breaker": (dict,), "instinct": (dict,), "allowed_families": (list,),
}
_BREAKER_TYPES = {"fail_threshold": (int,), "open_s": (int, float), "success_threshold": (int,)}
_INSTINCT_TYPES = {"enabled": (bool,), "url": (str,), "key_file": (str,), "deadline_ms": (int,),
                   "contract_ttl_s": (int, float), "feedback": (bool,), "engine_urls": (list,)}
_NODE_TYPES = {
    "url": (str,), "engine": (str,), "enabled": (bool,), "key_env": (str,), "key_file": (str,),
    "slots": (int,), "connect_timeout_s": (int, float), "ttft_timeout_s": (int, float),
    "prefill_tps_floor": (int, float), "idle_timeout_s": (int, float),
    "nonstream_timeout_s": (int, float), "gpu_lease": (bool,), "exclusive_group": (str,),
    "members": (list,), "labels": (list,), "allow_public": (bool,), "role": (str,),
}
_DEP_TYPES = {
    "node": (str,), "profile": (str,), "upstream": (str,), "ctx": (int,), "family": (str,),
    "caps": (list,), "conformance": (str,), "enabled": (bool,), "listed": (bool,),
    "verify_upstream": (bool,),
}
_ROUTE_TYPES = {
    "targets": (list,), "aliases": (list,), "spill": (bool,), "same_family": (bool,), "family": (str,),
    "affinity": (str,), "require": (list,), "min_ctx": (int,), "max_attempts": (int,),
    "retry_on_ttft_timeout": (bool,), "description": (str,), "listed": (bool,),
}
_TARGET_TYPES = {"d": (str,), "priority": (int,), "weight": (int, float)}
_WHEN_TYPES = {
    "model": (str, list), "device": (str, list), "role": (str, list), "has_images": (bool,),
    "has_tools": (bool,), "needs_json_schema": (bool,), "min_prompt_tokens": (int,),
    "hours": (str,), "task_class": (str, list),
}
_THEN_TYPES = {"route": (str,), "only_nodes": (list,), "ignore_nodes": (list,),
               "require": (list,), "prefer": (list,)}


def _typecheck(where: str, table: dict, types: dict, errors: list[str]) -> None:
    for k, v in table.items():
        if k not in types:
            errors.append(f"{where}: unknown key '{k}'")
            continue
        want = types[k]
        # bool is an int subclass: `slots = true` must not pass as 1.
        if isinstance(v, bool) and bool not in want:
            errors.append(f"{where}.{k}: expected {'/'.join(t.__name__ for t in want)}, got bool")
        elif not isinstance(v, want):
            errors.append(f"{where}.{k}: expected {'/'.join(t.__name__ for t in want)}, "
                          f"got {type(v).__name__}")


def _strlist(v) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, str):
        return (v,)
    return tuple(str(x) for x in v)


def host_class(url: str) -> tuple[str, str]:
    """(host, class) where class is loopback | private | tailnet | name | public | invalid."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "", "invalid"
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return host, "invalid"
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        return host, "invalid"
    try:
        _ = parts.port
    except ValueError:
        return host, "invalid"
    if host == "localhost":
        return host, "loopback"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host.endswith(".ts.net"):
            return host, "tailnet"
        if "." not in host:
            return host, "name"          # a MagicDNS / LAN short name
        return host, "public"
    if ip.is_loopback:
        return host, "loopback"
    if ip.version == 4 and ip in TAILNET:
        return host, "tailnet"
    if ip.is_private or ip.is_link_local:
        return host, "private"
    return host, "public"


def _norm_url(url: str) -> str:
    return url.rstrip("/")


def _endpoint_key(url: str) -> str:
    """Compare two URLs by the endpoint they reach: scheme, host, port.
    Every loopback spelling (localhost, 127.x, ::1) is one host, so a node at
    http://localhost:8094/ is still recognised as instinct's 127.0.0.1:8094."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(url.strip())
        host = (u.hostname or "").lower()
        port = u.port or {"http": 80, "https": 443}.get(u.scheme, 0)
    except ValueError:
        return _norm_url(url)
    try:
        if host == "localhost" or ipaddress.ip_address(host).is_loopback:
            host = "loopback"
    except ValueError:
        pass
    return f"{u.scheme.lower()}://{host}:{port}{u.path.rstrip('/')}"


def gate_read_timeout(env: dict) -> float:
    try:
        return float(env.get("OPENBEAST_EDGE_READ_TIMEOUT") or 600)
    except ValueError:
        return 600.0


def _key_file_errors(where: str, path: str, repo: Path) -> list[str]:
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = repo / p
    try:
        st = p.stat()
    except OSError as e:
        return [f"{where}: key_file {p} is not readable ({e.strerror or e})"]
    errs = []
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        errs.append(f"{where}: key_file {p} is mode {stat.S_IMODE(st.st_mode):o} — chmod 600 it")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        errs.append(f"{where}: key_file {p} is not owned by this user")
    return errs


def _load_profile(spec: str, engine: str):
    if str(PYLIB) not in sys.path:
        sys.path.insert(0, str(PYLIB))
    import obprofile  # noqa: WPS433 — optional dependency, only for `profile =`
    return obprofile.load(spec)


def _welltyped(table: dict, types: dict) -> dict:
    """The keys of `table` whose value has the declared type. A wrong-typed
    value is already an error; dropping it lets the range checks below run on
    the default instead of raising TypeError on (say) `down_after = "2"`."""
    return {k: v for k, v in table.items() if k in types
            and isinstance(v, types[k]) and not (isinstance(v, bool) and bool not in types[k])}


def _route_family(where: str, r: dict, targets: list, deps: dict, allowed: tuple[str, ...],
                  errors: list[str], warnings: list[str]) -> tuple[bool, str | None]:
    """(same_family, anchor) for one route; appends every policy error."""
    fam_of = {t.d: deps[t.d].family for t in targets}
    if allowed:
        for t in targets:
            if fam_of[t.d] not in allowed:
                errors.append(f"{where}.targets: {t.d} is family {fam_of[t.d]!r}, outside "
                              f"hydra.allowed_families ({', '.join(allowed)})")
    explicit = r.get("family") if isinstance(r.get("family"), str) else None
    same = r.get("same_family")
    if explicit is not None:
        if same is False:
            errors.append(f"{where}: family = {explicit!r} with same_family = false — pick one "
                          "(family implies same_family)")
            return False, None
        if targets and explicit not in fam_of.values():
            errors.append(f"{where}.family {explicit!r}: no target has that family")
        anchor = explicit
    elif same is True and targets:
        top = min(t.priority for t in targets)
        fams = sorted({fam_of[t.d] for t in targets if t.priority == top})
        if len(fams) > 1:
            errors.append(f"{where}: same_family, but the priority-{top} targets span {', '.join(fams)} — "
                          "set family = \"...\" (the anchor is never inferred from list order)")
            return True, None
        anchor = fams[0]
    else:
        return False, None
    for t in targets:
        if fam_of[t.d] != anchor:
            warnings.append(f"{where}.targets: {t.d} (family {fam_of[t.d]}) can never serve this "
                            f"same_family route (anchored on {anchor})")
    return True, anchor


def validate(raw: dict, env: dict | None = None, *, repo: Path = REPO,
             source: str = "hydra.toml") -> Config:
    """Validate a parsed hydra.toml. Raises ConfigError listing EVERY error.

    Never anything else: /hydra/reload and --check only understand
    ConfigError, so a crash here would 500 the reload and leave
    last_reload_error unset. Anything a check did not anticipate, on input
    that already failed a type check, is reported as those errors."""
    errors: list[str] = []
    try:
        return _validate(raw, env, repo, source, errors)
    except ConfigError:
        raise
    except (TypeError, AttributeError, ValueError, KeyError) as e:
        raise ConfigError(errors or [f"malformed config ({type(e).__name__}: {e})"]) from e


def _validate(raw, env, repo: Path, source: str, errors: list[str]) -> Config:
    env = dict(os.environ if env is None else env)
    warnings: list[str] = []
    if not isinstance(raw, dict):
        raise ConfigError(["config is not a table"])
    for k in raw:
        if k not in _TOP_KEYS:
            errors.append(f"unknown top-level key '{k}'")
    if raw.get("schema") != SCHEMA or isinstance(raw.get("schema"), bool):
        errors.append(f"schema must be {SCHEMA} (got {raw.get('schema')!r})")

    # [hydra]
    h = raw.get("hydra", {})
    if not isinstance(h, dict):
        errors.append("hydra: must be a table")
        h = {}
    _typecheck("hydra", h, _HYDRA_TYPES, errors)
    br = h.get("breaker", {}) if isinstance(h.get("breaker", {}), dict) else {}
    _typecheck("hydra.breaker", br, _BREAKER_TYPES, errors)
    ins = h.get("instinct", {}) if isinstance(h.get("instinct", {}), dict) else {}
    _typecheck("hydra.instinct", ins, _INSTINCT_TYPES, errors)
    hs = {k: v for k, v in _welltyped(h, _HYDRA_TYPES).items() if k not in ("breaker", "instinct")}
    ins = _welltyped(ins, _INSTINCT_TYPES)
    if "allowed_families" in hs:
        af = hs["allowed_families"]
        if not all(isinstance(x, str) and x for x in af):
            errors.append("hydra.allowed_families: every entry must be a non-empty family string")
            af = [x for x in af if isinstance(x, str) and x]
        hs["allowed_families"] = tuple(dict.fromkeys(af))
    if "engine_urls" in ins and not all(isinstance(u, str) for u in ins["engine_urls"]):
        errors.append("hydra.instinct.engine_urls: every entry must be a string URL")
        ins.pop("engine_urls")
    try:
        breaker = Breaker(**_welltyped(br, _BREAKER_TYPES))
        instinct = InstinctCfg(**{k: (tuple(v) if k == "engine_urls" else v) for k, v in ins.items()})
        settings = Settings(**hs, breaker=breaker, instinct=instinct)
    except TypeError as e:           # only reachable after a type error above
        errors.append(f"hydra: {e}")
        settings = Settings()
    s = settings
    gate_to = gate_read_timeout(env)
    if s.unknown_model not in ("default", "404"):
        errors.append("hydra.unknown_model must be 'default' or '404'")
    if not 1 <= s.probe_interval_s <= 60:
        errors.append("hydra.probe_interval_s must be 1..60")
    if s.probe_down_interval_s < s.probe_interval_s:
        errors.append("hydra.probe_down_interval_s must be >= probe_interval_s")
    if not 10 <= s.models_interval_s <= 3600:
        errors.append("hydra.models_interval_s must be 10..3600")
    for k in ("down_after", "up_after"):
        if not 1 <= getattr(s, k) <= 10:
            errors.append(f"hydra.{k} must be 1..10")
    if s.pre_commit_budget_s <= 0:
        errors.append("hydra.pre_commit_budget_s must be > 0")
    if s.pre_commit_budget_s >= gate_to:
        errors.append(f"hydra.pre_commit_budget_s {s.pre_commit_budget_s:g} must be < the gate read "
                      f"timeout {gate_to:g} (OPENBEAST_EDGE_READ_TIMEOUT)")
    elif s.pre_commit_budget_s >= 590:
        warnings.append(f"hydra.pre_commit_budget_s {s.pre_commit_budget_s:g} >= 590: the OpenAI SDK's "
                        "default timeout is 600 s, so its retry can race hydra's last attempt")
    if not 1.5 <= s.chars_per_token <= 6:
        errors.append("hydra.chars_per_token must be 1.5..6")
    if not 0 <= s.ctx_margin <= 0.5:
        errors.append("hydra.ctx_margin must be 0..0.5")
    if s.default_max_tokens < 1:
        errors.append("hydra.default_max_tokens must be >= 1")
    if s.affinity_max < 1 or s.affinity_ttl_s <= 0:
        errors.append("hydra.affinity_ttl_s / affinity_max must be positive")
    if s.audit_max_mb < 1:
        errors.append("hydra.audit_max_mb must be >= 1")
    if breaker.fail_threshold < 1 or breaker.success_threshold < 1 or breaker.open_s <= 0:
        errors.append("hydra.breaker values must be positive")
    if instinct.deadline_ms < 1 or instinct.deadline_ms > 2000:
        errors.append("hydra.instinct.deadline_ms must be 1..2000")
    _, icls = host_class(instinct.url)
    if icls != "loopback":
        errors.append(f"hydra.instinct.url {instinct.url!r} must be a loopback http URL "
                      "(instinct binds 127.0.0.1 only)")

    # [nodes]
    nodes: dict[str, Node] = {}
    rn = raw.get("nodes")
    if not isinstance(rn, dict) or not rn:
        errors.append("nodes: at least one [nodes.<id>] is required")
        rn = {}
    # Reconciliation §4 as revised 2026-09-30 (plan "Revision"): only the
    # instinct SERVICE is never a pool (that would be a loop: hydra asks
    # instinct, instinct is routed through hydra). An instinct ENGINE that is
    # also an inference node — the rig's own 27B scoring by logprobs — is no
    # loop: instinct calls it directly, never through hydra.
    instinct_service = _endpoint_key(instinct.url)
    instinct_engines = {_endpoint_key(u) for u in instinct.engine_urls}
    for nid, n in rn.items():
        where = f"nodes.{nid}"
        if not NODE_ID_RE.match(nid):
            errors.append(f"{where}: id must match {NODE_ID_RE.pattern}")
        if not isinstance(n, dict):
            errors.append(f"{where}: must be a table")
            continue
        _typecheck(where, n, _NODE_TYPES, errors)
        url, engine = n.get("url"), n.get("engine")
        if not isinstance(url, str):
            errors.append(f"{where}.url is required")
            url = ""
        if engine not in ENGINES:
            errors.append(f"{where}.engine must be one of {', '.join(ENGINES)} (got {engine!r})")
            engine = "openai"
        role = n.get("role", "inference")
        if role not in NODE_ROLES:
            errors.append(f"{where}.role must be one of {', '.join(NODE_ROLES)}")
        elif role == "instinct-engine":
            # Reconciliation §4 / instinct I7: an instinct engine is never a
            # routable pool — that would route instinct's own traffic through
            # hydra and back into itself.
            errors.append(f"{where}: role = \"instinct-engine\" — hydra refuses to route to an "
                          "instinct engine (docs/BEAST_INSTINCT_PLAN.md reconciliation §4)")
        host, cls = host_class(url) if url else ("", "invalid")
        allow_public = bool(n.get("allow_public", False))
        if cls == "invalid":
            errors.append(f"{where}.url {url!r} must be http(s)://host:port with no path")
        elif cls == "public" and not allow_public:
            errors.append(f"{where}.url host {host} is public — nodes must be loopback, RFC1918 or "
                          "tailnet (set allow_public = true to override)")
        elif cls == "name":
            warnings.append(f"{where}.url host {host!r} is a short name — hydra cannot verify it is "
                            "tailnet or LAN; prefer the tailnet IP or *.ts.net name")
        if allow_public:
            warnings.append(f"{where}: allow_public = true")
        if url and _endpoint_key(url) == instinct_service:
            errors.append(f"{where}.url is the instinct service URL — never a hydra pool "
                          "(reconciliation §4)")
        elif url and _endpoint_key(url) in instinct_engines:
            warnings.append(f"{where}.url is also an instinct engine (hydra.instinct.engine_urls): "
                            "instinct scores on it directly, so hydra's in-flight count cannot see "
                            "those calls — on a 1-slot node they queue with routed turns (plan "
                            "Revision 2026-09-30, risk 13)")
        loopback = cls == "loopback"
        if n.get("key_env") and n.get("key_file"):
            errors.append(f"{where}: set key_env OR key_file, not both")
        if engine == "tensorfold" and (n.get("key_env") or n.get("key_file")):
            errors.append(f"{where}: TensorFold has no auth — a key must never be sent to it "
                          "(firewall it to the rig instead)")
        if isinstance(n.get("key_file"), str):
            errors += _key_file_errors(where, n["key_file"], repo)
        if (not loopback and engine != "tensorfold" and not n.get("key_env")
                and not n.get("key_file") and cls != "invalid"):
            warnings.append(f"{where}: remote {engine} node with no key")
        slots = n.get("slots", 1)
        if isinstance(slots, int) and not isinstance(slots, bool) and slots < 1:
            errors.append(f"{where}.slots must be >= 1")
        ttft = n.get("ttft_timeout_s", 120.0)
        idle = n.get("idle_timeout_s", 120.0)
        nonstream = n.get("nonstream_timeout_s", s.pre_commit_budget_s)
        conn = n.get("connect_timeout_s", 3.0 if loopback else 5.0)
        for k, v in (("ttft_timeout_s", ttft), ("idle_timeout_s", idle),
                     ("nonstream_timeout_s", nonstream), ("connect_timeout_s", conn)):
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v <= 0:
                errors.append(f"{where}.{k} must be > 0")
        if isinstance(ttft, (int, float)) and ttft > s.pre_commit_budget_s:
            errors.append(f"{where}.ttft_timeout_s {ttft:g} > hydra.pre_commit_budget_s "
                          f"{s.pre_commit_budget_s:g}")
        if isinstance(idle, (int, float)) and idle >= gate_to:
            errors.append(f"{where}.idle_timeout_s {idle:g} must be < the gate read timeout {gate_to:g}")
        if isinstance(nonstream, (int, float)) and nonstream > s.pre_commit_budget_s:
            errors.append(f"{where}.nonstream_timeout_s {nonstream:g} > hydra.pre_commit_budget_s")
        pf = n.get("prefill_tps_floor")
        if pf is not None and isinstance(pf, (int, float)) and pf <= 0:
            errors.append(f"{where}.prefill_tps_floor must be > 0")
        if n.get("gpu_lease") and not loopback:
            errors.append(f"{where}: gpu_lease is only allowed on a loopback node (the lease is this box's)")
        try:
            nodes[nid] = Node(
                id=nid, url=_norm_url(url), engine=engine, enabled=bool(n.get("enabled", True)),
                key_env=n.get("key_env") or None, key_file=n.get("key_file") or None,
                slots=int(slots) if isinstance(slots, int) else 1,
                slots_known="slots" in n or source != "implicit",
                connect_timeout_s=float(conn), ttft_timeout_s=float(ttft),
                prefill_tps_floor=float(pf) if isinstance(pf, (int, float)) else None,
                idle_timeout_s=float(idle), nonstream_timeout_s=float(nonstream),
                gpu_lease=bool(n.get("gpu_lease", False)),
                exclusive_group=n.get("exclusive_group") or None,
                members=_strlist(n.get("members")), labels=_strlist(n.get("labels")),
                allow_public=allow_public, role=str(role), loopback=loopback)
        except (TypeError, ValueError) as e:
            errors.append(f"{where}: {e}")
    groups: dict[str, list[str]] = {}
    for n in nodes.values():
        if n.exclusive_group and n.enabled:
            groups.setdefault(n.exclusive_group, []).append(n.id)
    for g, members in sorted(groups.items()):
        if len(members) > 1:
            errors.append(f"exclusive_group {g!r}: {len(members)} enabled nodes ({', '.join(members)}) — "
                          "at most one may be enabled")

    # [deployments]
    deps: dict[str, Deployment] = {}
    rd = raw.get("deployments")
    if not isinstance(rd, dict) or not rd:
        errors.append("deployments: at least one [deployments.\"<id>\"] is required")
        rd = {}
    for did, d in rd.items():
        where = f"deployments.{did}"
        if not DEPLOYMENT_ID_RE.match(did):
            errors.append(f"{where}: id must match {DEPLOYMENT_ID_RE.pattern}")
        if not isinstance(d, dict):
            errors.append(f"{where}: must be a table")
            continue
        _typecheck(where, d, _DEP_TYPES, errors)
        node = nodes.get(d.get("node")) if isinstance(d.get("node"), str) else None
        if node is None:
            errors.append(f"{where}.node {d.get('node')!r} is not a configured node")
        upstream, ctx = d.get("upstream"), d.get("ctx")
        prof = d.get("profile")
        if isinstance(prof, str):
            try:
                p = _load_profile(prof, node.engine if node else "")
            except Exception as e:  # noqa: BLE001 — ProfileError, OSError, ImportError
                errors.append(f"{where}.profile {prof!r}: {e}")
                p = None
            if p is not None:
                if node is not None and p.backend != node.engine:
                    errors.append(f"{where}.profile {prof!r} is BACKEND={p.backend} but node "
                                  f"{node.id} is engine={node.engine}")
                p_up = p.get("SERVED_MODEL_NAME")
                p_ctx_raw = p.get("MAX_MODEL_LEN")
                p_ctx = int(p_ctx_raw) if p_ctx_raw.isdigit() else 0
                if upstream is not None and upstream != p_up:
                    errors.append(f"{where}.upstream {upstream!r} conflicts with profile "
                                  f"SERVED_MODEL_NAME {p_up!r}")
                if ctx is not None and ctx != p_ctx:
                    errors.append(f"{where}.ctx {ctx} conflicts with profile MAX_MODEL_LEN {p_ctx}")
                upstream = p_up if upstream is None else upstream
                ctx = p_ctx if ctx is None else ctx
                seqs = p.get("MAX_NUM_SEQS")
                if node is not None and seqs.isdigit() and int(seqs) != node.slots \
                        and "slots" not in (rn.get(node.id) or {}):
                    warnings.append(f"{where}: profile MAX_NUM_SEQS={seqs} but node {node.id} "
                                    f"slots={node.slots} (set slots explicitly)")
        if not isinstance(upstream, str) or not upstream:
            errors.append(f"{where}.upstream is required (or a profile)")
            upstream = ""
        if not isinstance(ctx, int) or isinstance(ctx, bool) or ctx < 0:
            errors.append(f"{where}.ctx is required (or a profile), an integer >= 0")
            ctx = 0
        if ctx == 0:
            warnings.append(f"{where}: ctx = 0 (unknown) — the context-fit filter is off for it")
        caps = d.get("caps", [])
        caps = list(caps) if isinstance(caps, list) else []
        for c in caps:
            if c not in CAPS:
                errors.append(f"{where}.caps: unknown capability {c!r} (known: {', '.join(CAPS)})")
            elif c in LLAMA_ONLY_CAPS and node is not None and node.engine != "llama":
                errors.append(f"{where}.caps: {c!r} is llama.cpp-only (node {node.id} is {node.engine})")
        conf_default = "off" if (node is None or node.loopback) else "required"
        conformance = d.get("conformance", conf_default)
        if conformance not in ("required", "advisory", "off"):
            errors.append(f"{where}.conformance must be required|advisory|off")
        deps[did] = Deployment(
            id=did, node=node.id if node else str(d.get("node")), upstream=upstream, ctx=int(ctx),
            family=d.get("family") or upstream, caps=frozenset(c for c in caps if c in CAPS),
            conformance=conformance if conformance in ("required", "advisory", "off") else "required",
            enabled=bool(d.get("enabled", True)),
            listed=bool(d.get("listed", s.list_deployments)), profile=prof if isinstance(prof, str) else None,
            verify_upstream=bool(d.get("verify_upstream", True)))
        if d.get("verify_upstream") is False:
            warnings.append(f"{where}: verify_upstream = false — a swapped model on the node will not be "
                            "detected (set the served id and drop it)")

    # [routes]
    routes: dict[str, Route] = {}
    rr = raw.get("routes")
    if not isinstance(rr, dict) or not rr:
        errors.append("routes: at least one [routes.<id>] is required")
        rr = {}
    for rid, r in rr.items():
        where = f"routes.{rid}"
        if not ROUTE_ID_RE.match(rid):
            errors.append(f"{where}: id must match {ROUTE_ID_RE.pattern}")
        if not isinstance(r, dict):
            errors.append(f"{where}: must be a table")
            continue
        _typecheck(where, r, _ROUTE_TYPES, errors)
        targets = []
        rt = r.get("targets")
        if not isinstance(rt, list) or not rt:
            errors.append(f"{where}.targets must be a non-empty list")
            rt = []
        for i, t in enumerate(rt):
            tw = f"{where}.targets[{i}]"
            if not isinstance(t, dict):
                errors.append(f"{tw}: must be an inline table {{ d = ... }}")
                continue
            _typecheck(tw, t, _TARGET_TYPES, errors)
            if t.get("d") not in deps:
                errors.append(f"{tw}.d {t.get('d')!r} is not a configured deployment")
                continue
            w = t.get("weight", 1.0)
            if not isinstance(w, (int, float)) or isinstance(w, bool) or w <= 0:
                errors.append(f"{tw}.weight must be > 0")
                w = 1.0
            pr = t.get("priority", 0)
            targets.append(Target(d=t["d"], priority=int(pr) if isinstance(pr, int) else 0, weight=float(w)))
        aff = r.get("affinity", "session")
        if aff not in ("session", "sticky", "none"):
            errors.append(f"{where}.affinity must be session|sticky|none")
        req = _strlist(r.get("require"))
        for c in req:
            if c not in CAPS:
                errors.append(f"{where}.require: unknown capability {c!r}")
        ma = r.get("max_attempts", 3)
        if not isinstance(ma, int) or not 1 <= ma <= 6:
            errors.append(f"{where}.max_attempts must be 1..6")
            ma = 3
        mc = r.get("min_ctx", 0)
        same_family, anchor = _route_family(where, r, targets, deps, s.allowed_families, errors, warnings)
        routes[rid] = Route(
            id=rid, targets=tuple(targets), aliases=_strlist(r.get("aliases")),
            spill=bool(r.get("spill", True)), same_family=same_family, family=anchor,
            affinity=aff if aff in ("session", "sticky", "none") else "session", require=req,
            min_ctx=mc if isinstance(mc, int) else 0, max_attempts=ma,
            retry_on_ttft_timeout=bool(r.get("retry_on_ttft_timeout", False)),
            description=str(r.get("description", "")), listed=bool(r.get("listed", True)))
        if targets and not any(deps[t.d].enabled and nodes.get(deps[t.d].node, None) is not None
                               and nodes[deps[t.d].node].enabled for t in targets):
            warnings.append(f"{where}: every target is disabled")
    if routes and s.default_route not in routes:
        errors.append(f"hydra.default_route {s.default_route!r} is not a configured route")
    routed = {t.d for r in routes.values() for t in r.targets}
    if s.allowed_families:
        for did, d in deps.items():
            if d.family not in s.allowed_families and did not in routed:
                warnings.append(f"deployments.{did}: family {d.family!r} is outside hydra.allowed_families "
                                "— never routed; reachable only as a strict pin")
    else:
        fams = sorted({deps[d].family for d in routed if d in deps})
        if len(fams) > 1:
            # Max 2026-09-30: "all of our models are uncensored" — a fleet rule.
            # hydra can only enforce it when the families are declared, so a
            # config whose routes mix families must say which ones may answer.
            # (A pre-policy hydra.toml that spilled beast to stock lands here.)
            errors.append(f"hydra.allowed_families is required: routes can answer from any of "
                          f"{', '.join(fams)} — list the families a route may ever use")

    # the id namespace: routes, aliases and deployments must never collide
    by_id: dict[str, Route] = {}
    seen: dict[str, str] = {d: f"deployment {d}" for d in deps}
    for rid, r in routes.items():
        for name in (rid, *r.aliases):
            if name in seen:
                errors.append(f"id {name!r} is claimed by both {seen[name]} and route {rid}")
                continue
            seen[name] = f"route {rid}" + ("" if name == rid else " (alias)")
            by_id[name] = r

    # [[rules]]
    rules: list[Rule] = []
    rl = raw.get("rules", [])
    if not isinstance(rl, list):
        errors.append("rules must be an array of tables ([[rules]])")
        rl = []
    names: set[str] = set()
    for i, r in enumerate(rl):
        where = f"rules[{i}]"
        if not isinstance(r, dict):
            errors.append(f"{where}: must be a table")
            continue
        for k in r:
            if k not in ("name", "when", "then"):
                errors.append(f"{where}: unknown key '{k}'")
        name = r.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{where}.name is required")
            name = f"rule-{i}"
        elif name in names:
            errors.append(f"{where}.name {name!r} is not unique")
        names.add(name)
        where = f"rules.{name}"
        when, then = r.get("when", {}), r.get("then", {})
        if not isinstance(when, dict) or not isinstance(then, dict):
            errors.append(f"{where}: when/then must be tables")
            continue
        if not then:
            errors.append(f"{where}.then is empty")
        _typecheck(f"{where}.when", when, _WHEN_TYPES, errors)
        _typecheck(f"{where}.then", then, _THEN_TYPES, errors)
        if isinstance(when.get("hours"), str) and not HOURS_RE.match(when["hours"]):
            errors.append(f"{where}.when.hours {when['hours']!r} must be HH:MM-HH:MM")
        if "route" in then:
            if then["route"] not in routes:
                errors.append(f"{where}.then.route {then['route']!r} is not a configured route")
            if "model" not in when:
                warnings.append(f"{where}: then.route with no when.model redirects EVERY route")
        for k in ("only_nodes", "ignore_nodes"):
            for nid in _strlist(then.get(k)):
                if nid not in nodes:
                    errors.append(f"{where}.then.{k}: {nid!r} is not a configured node")
        for c in _strlist(then.get("require")):
            if c not in CAPS:
                errors.append(f"{where}.then.require: unknown capability {c!r}")
        for did in _strlist(then.get("prefer")):
            if did not in deps:
                errors.append(f"{where}.then.prefer: {did!r} is not a configured deployment")
        wm = when.get("model")
        sources = (list(routes.values()) if "model" not in when
                   else [by_id[m] for m in _strlist(wm) if m in by_id] if isinstance(wm, (str, list)) else [])
        dest = routes.get(then["route"]) if isinstance(then.get("route"), str) else None
        if dest is not None:
            for src in dict.fromkeys(sources):
                if src.id == dest.id or not src.same_family:
                    continue
                if dest.same_family and dest.family != src.family:
                    errors.append(f"{where}: sends same_family route {src.id} (family {src.family}) to "
                                  f"{dest.id}, anchored on {dest.family} — every such request would 503")
                elif not any(deps[t.d].family == src.family for t in dest.targets):
                    errors.append(f"{where}: sends same_family route {src.id} (family {src.family}) to "
                                  f"{dest.id}, which has no {src.family} target — every such request "
                                  "would 503")
        reach = [dest] if dest is not None else sources
        for did in _strlist(then.get("prefer")):
            if did in deps and reach and not any(t.d == did for r_ in reach for t in r_.targets):
                warnings.append(f"{where}.then.prefer: {did!r} is not a target of any route this rule "
                                "applies to — it does nothing")
        rules.append(Rule(name=name, when=dict(when), then=dict(then)))

    if errors:
        raise ConfigError(errors, warnings)
    cfg = Config(settings=s, nodes=nodes, deployments=deps, routes=routes, rules=rules,
                 warnings=warnings, source=source, route_by_id_or_alias=by_id)
    cfg.hash = config_hash(cfg)
    return cfg


def load_config(path: str | os.PathLike, env: dict | None = None, *, repo: Path = REPO) -> Config:
    p = Path(path)
    try:
        raw = tomllib.loads(p.read_text())
    except OSError as e:
        raise ConfigError([f"cannot read {p}: {e.strerror or e}"]) from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError([f"{p}: TOML syntax: {e}"]) from e
    return validate(raw, env, repo=repo, source=str(p))


def _env(env: dict, *names: str) -> str:
    for n in names:
        v = (env.get(n) or "").strip()
        if v:
            return v
    return ""


def implicit_raw(env: dict | None = None) -> dict:
    """The single-node config HYDRA=true uses when hydra.toml is absent (plan §6.2).

    It behaves like today's stack: one node (the engine this rig manages),
    one deployment, one route `beast` with the legacy ids as aliases.
    """
    env = dict(os.environ if env is None else env)
    url = _env(env, "OPENBEAST_HYDRA_UPSTREAM_URL", "INFERENCE_URL", "OPENBEAST_INFERENCE_URL") \
        or "http://127.0.0.1:8080"
    engine = (_env(env, "INFERENCE_BACKEND", "OPENBEAST_INFERENCE_BACKEND") or "llama").lower()
    if engine not in ENGINES:
        engine = "llama"
    slots_raw = _env(env, "INFERENCE_SLOTS", "OPENBEAST_INFERENCE_SLOTS")
    # NOT OPENBEAST_INFERENCE_MODEL: under HYDRA=true conf.sh points that at
    # the route id (`beast`) so runner.py sends a routable name.
    known = _env(env, "OPENBEAST_HYDRA_UPSTREAM_MODEL", "INFERENCE_MODEL")
    upstream = known or "local"
    default = _env(env, "HYDRA_DEFAULT_MODEL", "OPENBEAST_HYDRA_DEFAULT_MODEL") or "beast"
    if not ROUTE_ID_RE.match(default):
        default = "beast"
    node: dict[str, Any] = {"url": url, "engine": engine}
    # `slots` only when the conf states it. Unset, the serve script's -np is
    # unknown here: validate() then routes on 1 and marks the count unknown,
    # so a gate-assigned id_slot reaches a multi-slot llama-server untouched.
    if slots_raw.isdigit() and int(slots_raw) > 0:
        node["slots"] = int(slots_raw)
    if engine != "tensorfold":
        node["key_env"] = "LLAMA_API_KEY"
    # No gpu_lease here: "behaves like today" means chat is not drained while
    # a campaign holds the lease. An explicit hydra.toml opts in (runbook §7).
    caps = [c for c in CAPS if engine == "llama" or c not in LLAMA_ONLY_CAPS]
    aliases = [a for a in ("qwen-27b-q5", "default", "local") if a != default]
    return {
        "schema": SCHEMA,
        "hydra": {"default_route": default},
        "nodes": {"rig": node},
        "deployments": {"local@rig": {"node": "rig", "upstream": upstream, "ctx": 0,
                                      "caps": caps, "conformance": "off",
                                      # an unknown served id is a guess: never judge it
                                      **({} if known else {"verify_upstream": False})}},
        "routes": {default: {"description": "This rig's engine (implicit config)",
                             "targets": [{"d": "local@rig", "priority": 0}], "aliases": aliases}},
    }


def implicit_config(env: dict | None = None, *, repo: Path = REPO) -> Config:
    raw = implicit_raw(env)
    cfg = validate(raw, env, repo=repo, source="implicit")
    # llama-server ignores the request `model`, so the implicit config never
    # judges the served id — even when conf.sh named one — and its ctx = 0 is
    # by design. Neither is worth a warning on every start.
    d = cfg.deployments["local@rig"]
    cfg.deployments["local@rig"] = Deployment(**{**d.__dict__, "verify_upstream": False})
    cfg.warnings = [w for w in cfg.warnings if "ctx = 0" not in w and "verify_upstream" not in w]
    cfg.hash = config_hash(cfg)
    return cfg


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(type(v))


def _toml_key(k: str) -> str:
    return k if re.match(r"^[A-Za-z0-9_-]+$", k) else json.dumps(k)


def to_toml(raw: dict, header: str = "") -> str:
    """Serialize the (shallow) hydra.toml shape. Round-trips through tomllib."""
    out = [header.rstrip("\n")] if header else []
    out.append(f"schema = {raw['schema']}")
    for table in ("hydra",):
        if raw.get(table):
            out += ["", f"[{table}]"]
            out += [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in raw[table].items()
                    if not isinstance(v, dict)]
            for k, v in raw[table].items():
                if isinstance(v, dict):
                    out += ["", f"[{table}.{k}]"] + [f"{_toml_key(a)} = {_toml_value(b)}" for a, b in v.items()]
    for table in ("nodes", "deployments", "routes"):
        for name, body in (raw.get(table) or {}).items():
            out += ["", f"[{table}.{_toml_key(name)}]"]
            out += [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in body.items()]
    for r in raw.get("rules") or []:
        out += ["", "[[rules]]"] + [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in r.items()]
    return "\n".join(out) + "\n"


def config_hash(cfg: Config) -> str:
    """sha256 of the normalized config (plan §6.2.3). Key CONTENTS never enter it."""
    def norm(o):
        if isinstance(o, (frozenset, set)):
            return sorted(o)
        if isinstance(o, tuple):
            return [norm(x) for x in o]
        if isinstance(o, list):
            return [norm(x) for x in o]
        if isinstance(o, dict):
            return {k: norm(v) for k, v in o.items()}
        if hasattr(o, "__dataclass_fields__"):
            return {k: norm(getattr(o, k)) for k in o.__dataclass_fields__}
        return o
    doc = {"settings": norm(cfg.settings), "nodes": norm(cfg.nodes),
           "deployments": norm(cfg.deployments), "routes": norm(cfg.routes),
           "rules": [norm(r) for r in cfg.rules]}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


# ───────────────────────────────── readiness parity ────────────────────────────────

def engine_ready(engine: str, status: int | None, body: bytes | str | None) -> str:
    """ready | loading | down — the Python twin of ob_backend_ready (backend.sh).

    bash uses `curl -fsS`, which fails on any HTTP status >= 400 and accepts
    everything below, then (llama, tensorfold) greps the body with a regex —
    not a JSON parse. This mirrors that exactly; the parity test holds us to it.
    """
    if status is None:
        return "down"
    text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else (body or "")
    ok_http = 100 <= status < 400
    if engine == "llama":
        if ok_http and _READY_STATUS_RE.search(text):
            return "ready"
        if status == 503 and "Loading model" in text:
            return "loading"
        return "down"
    if engine == "tensorfold":
        if ok_http and (_READY_OK_TRUE_RE.search(text) or _READY_STATUS_RE.search(text)):
            return "ready"
        return "down"
    # vllm (empty 200) and generic openai: any non-error status is ready
    return "ready" if ok_http else "down"


def conformance_verdict(report: dict | None, node_url: str) -> tuple[str, bool]:
    """(pass|fail|missing, tools_failed) from a conformance latest.json.

    A report only counts for THIS node when its url's host:port matches —
    a stale report from another box must not admit a deployment.
    """
    if not isinstance(report, dict):
        return "missing", False
    want = urlsplit(node_url)
    got = urlsplit(str(report.get("url") or ""))
    if (want.hostname, want.port) != (got.hostname, got.port):
        return "fail", False
    tools_failed = False
    for r in report.get("results") or []:
        if isinstance(r, dict) and r.get("name") == "tools" and r.get("status") == "fail":
            tools_failed = True
    return ("pass" if report.get("ok") is True else "fail"), tools_failed


# ─────────────────────────────────── health model ──────────────────────────────────

@dataclass
class DeploymentHealth:
    state: str = UNKNOWN
    ok_streak: int = 0
    fail_streak: int = 0
    breaker: str = CLOSED
    fails: int = 0
    opened_at: float = 0.0
    half_open_ok: int = 0
    trial_inflight: int = 0
    served_total: int = 0
    fail_total: int = 0
    ttft_ewma_ms: float | None = None
    last_change: float = 0.0
    detail: str = ""


class HealthState:
    """Hysteresis + circuit breaker for one deployment (plan §6.5). Pure; time is passed in."""

    def __init__(self, s: Settings, h: DeploymentHealth | None = None):
        self.s = s
        self.h = h or DeploymentHealth()

    def _set(self, state: str, now: float, detail: str = "") -> None:
        if self.h.state != state:
            self.h.state, self.h.last_change = state, now
        self.h.detail = detail

    # probes
    def on_probe(self, result: str, now: float) -> str:
        h = self.h
        if result == "ready":
            h.ok_streak, h.fail_streak = h.ok_streak + 1, 0
            if h.state == LOADING:
                self._set(READY, now)
            elif h.state in (UNKNOWN, DOWN) and h.ok_streak >= self.s.up_after:
                self._set(READY, now)
        elif result == "loading":
            h.ok_streak = h.fail_streak = 0
            if h.state not in (AUTH_FAILED, MISMATCH):
                self._set(LOADING, now, "engine loading")
        else:
            h.fail_streak, h.ok_streak = h.fail_streak + 1, 0
            if h.state in (READY, LOADING, UNKNOWN) and h.fail_streak >= self.s.down_after:
                self._set(DOWN, now, f"{h.fail_streak} failed probes")
        return h.state

    def on_models(self, result: str, now: float, detail: str = "",
                  started: float | None = None) -> str:
        """result: ok | auth | mismatch | error (error = no information).

        `started` is when the /v1/models check was SENT. An "ok" that was in
        flight when a request observed a 401/403 or a model 404 is stale: it
        must not clear the newer AUTH_FAILED / MISMATCH (a check fired at boot
        re-admitted a node whose key had just been refused)."""
        h = self.h
        if result == "auth":
            self._set(AUTH_FAILED, now, detail or "401/403 from the node")
        elif result == "mismatch":
            self._set(MISMATCH, now, detail or "upstream id not served")
        elif (result == "ok" and h.state in (AUTH_FAILED, MISMATCH)
              and (started is None or h.last_change <= started)):
            # health said ready (we only check models on a live node): resume
            self._set(READY if h.ok_streak >= 1 else UNKNOWN, now)
        return h.state

    # breaker (requests only — probes never close it)
    def breaker_state(self, now: float) -> str:
        h = self.h
        if h.breaker == OPEN and now - h.opened_at >= self.s.breaker.open_s:
            h.breaker, h.half_open_ok, h.trial_inflight = HALF_OPEN, 0, 0
        return h.breaker

    def record_failure(self, now: float) -> None:
        h = self.h
        h.fail_total += 1
        st = self.breaker_state(now)
        if st == HALF_OPEN:
            h.breaker, h.opened_at, h.fails = OPEN, now, 0
            return
        h.fails += 1
        if h.fails >= self.s.breaker.fail_threshold:
            h.breaker, h.opened_at, h.fails = OPEN, now, 0

    def record_success(self, now: float) -> None:
        h = self.h
        h.served_total += 1
        st = self.breaker_state(now)
        if st == HALF_OPEN:
            h.half_open_ok += 1
            if h.half_open_ok >= self.s.breaker.success_threshold:
                h.breaker, h.fails = CLOSED, 0
        else:
            h.fails = 0

    def request_state(self, status_kind: str, now: float, detail: str = "") -> None:
        """A proxied request taught us something about the node's state."""
        if status_kind == "loading":
            self._set(LOADING, now, "503 Loading model on a request")
            self.h.ok_streak = 0
        elif status_kind == "auth":
            self._set(AUTH_FAILED, now, detail)
        elif status_kind == "mismatch":
            self._set(MISMATCH, now, detail)

    def admit_reason(self, now: float) -> str | None:
        """None when this deployment may take a request; otherwise why not."""
        h = self.h
        if h.state != READY:
            return h.state + (f" ({h.detail})" if h.detail else "")
        st = self.breaker_state(now)
        if st == OPEN:
            return f"breaker OPEN ({max(0.0, self.s.breaker.open_s - (now - h.opened_at)):.0f}s left)"
        if st == HALF_OPEN and h.trial_inflight >= 1:
            return "breaker HALF_OPEN (trial in flight)"
        return None


class AffinityLRU:
    """session key → deployment id, LRU-bounded with a TTL."""

    def __init__(self, max_items: int = 4096, ttl_s: float = 1800.0):
        self.max, self.ttl = max_items, ttl_s
        self._d: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def get(self, key: str | None, now: float | None = None) -> str | None:
        if not key:
            return None
        now = time.monotonic() if now is None else now
        v = self._d.get(key)
        if v is None:
            return None
        if now - v[1] > self.ttl:
            del self._d[key]
            return None
        self._d.move_to_end(key)
        return v[0]

    def put(self, key: str | None, dep: str, now: float | None = None) -> None:
        if not key:
            return
        now = time.monotonic() if now is None else now
        self._d[key] = (dep, now)
        self._d.move_to_end(key)
        while len(self._d) > self.max:
            self._d.popitem(last=False)

    def __len__(self) -> int:
        return len(self._d)


class Admission:
    """One held in-flight unit. release() is idempotent (edge.py discipline).

    `hs` is the deployment's HealthState AT ADMISSION: a reload that drops or
    renames the deployment mid-request must not turn the bookkeeping of the
    request already in flight into a KeyError (and a leaked unit)."""

    def __init__(self, fleet: "FleetState", d: str, node: str, trial: bool, hs: "HealthState"):
        self._fleet, self.d, self.node, self.trial, self.done = fleet, d, node, trial, False
        self.hs = hs

    def release(self) -> None:
        if self.done:
            return
        self.done = True
        f = self._fleet
        f._inflight[self.d] = max(0, f._inflight.get(self.d, 0) - 1)
        f._node_inflight[self.node] = max(0, f._node_inflight.get(self.node, 0) - 1)
        if self.trial:
            self.hs.h.trial_inflight = max(0, self.hs.h.trial_inflight - 1)


class FleetState:
    """Mutable routing state: health, in-flight, drain, conformance, affinity."""

    def __init__(self, cfg: Config, rng: random.Random | None = None):
        self.health: dict[str, HealthState] = {}
        self._inflight: dict[str, int] = {}
        self._node_inflight: dict[str, int] = {}
        self.drained: dict[str, str] = {}              # node -> manual | lease
        self.conformance: dict[str, tuple[str, bool]] = {}   # d -> (verdict, tools_failed)
        self.affinity = AffinityLRU(cfg.settings.affinity_max, cfg.settings.affinity_ttl_s)
        self.rng = rng or random.Random()
        self.adopt(cfg)

    def adopt(self, cfg: Config) -> None:
        """Take a (re)loaded config; keep what we learned about deployments that remain."""
        self.cfg = cfg
        keep = {}
        for did in cfg.deployments:
            old = self.health.get(did)
            keep[did] = HealthState(cfg.settings, old.h if old else None)
        self.health = keep
        self.drained = {n: r for n, r in self.drained.items() if n in cfg.nodes}
        self.affinity.max, self.affinity.ttl = cfg.settings.affinity_max, cfg.settings.affinity_ttl_s

    def inflight(self, d: str) -> int:
        return self._inflight.get(d, 0)

    def node_inflight(self, n: str) -> int:
        return self._node_inflight.get(n, 0)

    def admit(self, d: str, node: str, now: float) -> Admission:
        """Take an in-flight unit unconditionally (decide() already vetted it)."""
        hs = self.health.get(d)
        if hs is None:              # dropped by a reload after decide(): count it, judge nothing
            hs = HealthState(self.cfg.settings)
        trial = hs.breaker_state(now) == HALF_OPEN
        if trial:
            hs.h.trial_inflight += 1
        self._inflight[d] = self._inflight.get(d, 0) + 1
        self._node_inflight[node] = self._node_inflight.get(node, 0) + 1
        return Admission(self, d, node, trial, hs)

    def try_admit(self, d: str, node: str, now: float) -> tuple[Admission | None, str | None]:
        """Admit only if the deployment may take a request NOW.

        decide() plans every failover attempt up front; by the time attempt 2
        runs, attempt 1 has awaited seconds and the world has moved: the
        target may have gone DOWN, its node may be drained, or another
        request may already hold its single HALF_OPEN trial (plan §6.5:
        exactly one). Returns (admission, None) or (None, why)."""
        if node in self.drained:
            return None, f"node {node} drained ({self.drained[node]})"
        hs = self.health.get(d)
        if hs is not None:
            why = hs.admit_reason(now)
            if why:
                return None, why
        return self.admit(d, node, now), None

    def effective_caps(self, dep: Deployment) -> frozenset:
        verdict = self.conformance.get(dep.id)
        if verdict and verdict[1]:
            return dep.caps - {"tools"}
        return dep.caps

    def conformance_reason(self, dep: Deployment) -> str | None:
        if dep.conformance != "required":
            return None
        verdict = self.conformance.get(dep.id, ("missing", False))[0]
        if verdict != "pass":
            return f"conformance required, report {verdict}"
        return None


# ─────────────────────────────────── features ───────────────────────────────────

@dataclass
class Features:
    path: str = "/v1/chat/completions"
    model: str = ""
    stream: bool = False
    has_images: bool = False
    has_tools: bool = False
    needs_json_schema: bool = False
    needs_grammar: bool = False
    needs_reasoning_budget: bool = False
    needs_embeddings: bool = False
    est_prompt_tokens: int = 0
    max_tokens: int = 0
    session_key: str | None = None
    pin: str | None = None
    prompt_head: str = ""

    def public(self) -> dict:
        """What the audit and traces may carry (no prompt text)."""
        return {"stream": self.stream, "est_prompt_tokens": self.est_prompt_tokens,
                "max_tokens": self.max_tokens, "has_tools": self.has_tools,
                "has_images": self.has_images, "needs_json_schema": self.needs_json_schema,
                "needs_grammar": self.needs_grammar, "needs_embeddings": self.needs_embeddings}


@dataclass
class Caller:
    trusted: bool = False
    device: str | None = None
    role: str | None = None


def _any_image_part(messages) -> bool:
    if not isinstance(messages, list):
        return False
    for m in messages:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and (part.get("type") in ("image_url", "input_image", "image")
                                               or "image_url" in part):
                    return True
    return False


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict)
                       and isinstance(p.get("text"), str))
    return ""


def _prompt_head(messages) -> str:
    """≤2,000 chars of the last user turn: first 500 + last 1,500 (instinct-route/1)."""
    if not isinstance(messages, list):
        return ""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            t = _text_of(m.get("content"))
            return t if len(t) <= 2000 else t[:500] + t[-1500:]
    return ""


def _header(headers, name: str) -> str | None:
    v = headers.get(name) if hasattr(headers, "get") else None
    if v is None and isinstance(headers, dict):
        low = {k.lower(): x for k, x in headers.items()}
        v = low.get(name.lower())
    return v


def extract_features(path: str, body: dict, headers, cfg: Config) -> Features:
    msgs = body.get("messages")
    if msgs is None:
        msgs = body.get("prompt")
    if msgs is None:
        msgs = body.get("input")
    if msgs is None:
        msgs = ""
    text = json.dumps(msgs, ensure_ascii=False) + json.dumps(body.get("tools") or [], ensure_ascii=False)
    rf = body.get("response_format") if isinstance(body.get("response_format"), dict) else {}
    mt = body.get("max_tokens") or body.get("max_completion_tokens") or cfg.settings.default_max_tokens
    try:
        mt = int(mt)
    except (TypeError, ValueError):
        mt = cfg.settings.default_max_tokens
    session = _header(headers, "x-conversation-id") or _header(headers, "x-hydra-session")
    if not session and isinstance(body.get("messages"), list):
        first_sys = next((_text_of(m.get("content")) for m in body["messages"]
                          if isinstance(m, dict) and m.get("role") == "system"), "")
        first_user = next((_text_of(m.get("content")) for m in body["messages"]
                           if isinstance(m, dict) and m.get("role") == "user"), "")
        if first_sys or first_user:
            session = hashlib.sha1((first_sys + "\x00" + first_user).encode()).hexdigest()[:16]
    return Features(
        path=path, model=str(body.get("model") or ""), stream=bool(body.get("stream")),
        has_images=_any_image_part(body.get("messages")),
        has_tools=bool(body.get("tools")),
        needs_json_schema=rf.get("type") in ("json_schema", "json_object") or "json_schema" in body,
        needs_grammar="grammar" in body,
        needs_reasoning_budget="reasoning_budget_tokens" in body,
        needs_embeddings=path.endswith("/embeddings"),
        est_prompt_tokens=math.ceil(len(text) / cfg.settings.chars_per_token),
        max_tokens=max(0, mt), session_key=session or None,
        pin=_header(headers, "x-hydra-pin") or None,
        prompt_head=_prompt_head(body.get("messages")))


def implied_caps(f: Features) -> set[str]:
    need = set()
    if f.has_images:
        need.add("vision")
    if f.has_tools:
        need.add("tools")
    if f.needs_json_schema:
        need.add("json_schema")
    if f.needs_grammar:
        need.add("grammar")
    if f.needs_reasoning_budget:
        need.add("reasoning_budget")
    if f.needs_embeddings:
        need.add("embeddings")
    return need


# ─────────────────────────────────── decision ───────────────────────────────────

@dataclass
class Candidate:
    d: Deployment
    n: Node
    prio: int
    weight: float


@dataclass
class Trace:
    requested: str = ""
    route: str | None = None
    strict: bool = False
    rules: list[str] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    task_class: str | None = None

    def note(self, s: str) -> None:
        self.notes.append(s)

    def as_dict(self) -> dict:
        return {"requested": self.requested, "route": self.route, "strict": self.strict,
                "rules": list(self.rules), "excluded": dict(self.excluded),
                "notes": list(self.notes), "task_class": self.task_class}


@dataclass
class Decision:
    status: int
    route: str | None
    attempts: list[Candidate]
    trace: Trace
    error_type: str | None = None
    message: str = ""
    retry_after: int | None = None
    strict: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 200

    @classmethod
    def err(cls, status: int, etype: str, t: Trace, message: str = "",
            retry_after: int | None = None) -> "Decision":
        return cls(status=status, route=t.route, attempts=[], trace=t, error_type=etype,
                   message=message or etype, retry_after=retry_after, strict=t.strict)

    def as_dict(self) -> dict:
        return {"status": self.status, "route": self.route, "strict": self.strict,
                "error": self.error_type, "message": self.message,
                "attempts": [{"d": c.d.id, "node": c.n.id, "engine": c.n.engine, "priority": c.prio}
                             for c in self.attempts],
                "trace": self.trace.as_dict()}


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_hours(spec: str, local_minutes: int) -> bool:
    a, b = spec.split("-")
    start, end = _minutes(a), _minutes(b)
    if start <= end:
        return start <= local_minutes < end
    return local_minutes >= start or local_minutes < end        # wraps midnight


def _matches(when: dict, f: Features, caller: Caller, local_minutes: int, requested: set[str],
             task_class: str | None) -> bool:
    for k, v in when.items():
        if k == "model":
            if not requested & set(_strlist(v)):
                return False
        elif k == "device":
            if not caller.trusted or caller.device not in _strlist(v):
                return False
        elif k == "role":
            if not caller.trusted or caller.role not in _strlist(v):
                return False
        elif k == "has_images":
            if f.has_images != v:
                return False
        elif k == "has_tools":
            if f.has_tools != v:
                return False
        elif k == "needs_json_schema":
            if f.needs_json_schema != v:
                return False
        elif k == "min_prompt_tokens":
            if f.est_prompt_tokens < v:
                return False
        elif k == "hours":
            if not in_hours(v, local_minutes):
                return False
        elif k == "task_class":
            # Only an ENFORCED instinct answer populates task_class; without
            # one the condition is simply false (reconciliation §1).
            if task_class is None or task_class not in _strlist(v):
                return False
        else:
            return False
    return True


def effective_ttft(node: Node, est_prompt_tokens: int, budget_s: float) -> float:
    t = node.ttft_timeout_s
    if node.prefill_tps_floor:
        t += est_prompt_tokens / node.prefill_tps_floor
    return min(t, budget_s)


def policy_reason(cfg: Config, d: Deployment, route: Route | None = None) -> str | None:
    """Why the family policy forbids `d` for a NON-strict request, or None.

    hydra.allowed_families first (fleet-wide), then the route's same_family
    anchor. Applied to every candidate — first choice, spill, failover, the
    ctx last resort, sticky affinity — so no path can reach a family the
    policy forbids. Strict pins never pass through here.
    """
    allowed = cfg.settings.allowed_families
    if allowed and d.family not in allowed:
        return f"family {d.family} not in hydra.allowed_families"
    if route is not None and route.same_family and route.family and d.family != route.family:
        return f"family {d.family} != {route.family} (route {route.id} same_family)"
    return None


def _exclude(d: Deployment, n: Node, state: FleetState, f: Features, need: set[str], route: Route | None,
             eff: dict, now: float, cfg: Config, check_ctx: bool = True) -> str | None:
    if route is not None:
        why = policy_reason(cfg, d, route)
        if why:
            return why
    if not n.enabled:
        return f"node {n.id} disabled"
    if not d.enabled:
        return "deployment disabled"
    if n.id in state.drained:
        return f"node {n.id} drained ({state.drained[n.id]})"
    hs = state.health.get(d.id)
    why = hs.admit_reason(now) if hs else "no health state"
    if why:
        return why
    why = state.conformance_reason(d)
    if why:
        return why
    missing = need - state.effective_caps(d)
    if missing:
        return f"missing caps {','.join(sorted(missing))}"
    if check_ctx and d.ctx:
        want = f.est_prompt_tokens + f.max_tokens
        room = int(d.ctx * (1 - cfg.settings.ctx_margin))
        if want > room:
            return f"ctx {d.ctx} (usable {room}) < need {want}"
    if route is not None and route.min_ctx and d.ctx < route.min_ctx:
        return f"ctx {d.ctx} < route min_ctx {route.min_ctx}"
    only = eff.get("only_nodes")
    if only and n.id not in only[0]:
        return f"node {n.id} not in only_nodes (rule {only[1]})"
    ign = eff.get("ignore_nodes")
    if ign and n.id in ign[0]:
        return f"node {n.id} in ignore_nodes (rule {ign[1]})"
    return None


def _strict(cfg: Config, state: FleetState, f: Features, d: Deployment, t: Trace, now: float) -> Decision:
    t.strict, t.route = True, "pin"
    n = cfg.nodes[d.node]
    missing = implied_caps(f) - state.effective_caps(d)
    if missing:
        # Never strip a field to make a pin fit: that would silently change
        # what an eval measured.
        t.excluded[d.id] = f"missing caps {','.join(sorted(missing))}"
        return Decision.err(422, "hydra_pin_incompatible", t,
                            f"deployment {d.id} lacks {', '.join(sorted(missing))}")
    # ctx is NOT checked on a pin: the engine's own overflow 400 must reach
    # the caller verbatim (runner.py compacts on it).
    why = _exclude(d, n, state, f, set(), None, {}, now, cfg, check_ctx=False)
    if why:
        t.excluded[d.id] = why
        return Decision.err(503, "hydra_pinned_unavailable", t,
                            f"pinned deployment {d.id} is unavailable: {why}", retry_after=5)
    return Decision(status=200, route="pin", attempts=[Candidate(d, n, 0, 1.0)], trace=t, strict=True)


def resolve_route(cfg: Config, model: str) -> Route | None:
    return cfg.route_by_id_or_alias.get(model)


def decide(cfg: Config, state: FleetState, f: Features, caller: Caller, now: float,
           pin: str | None = None, *, local_minutes: int | None = None,
           task_class: str | None = None) -> Decision:
    """Pure routing decision (plan §6.4). `now` is monotonic seconds."""
    t = Trace(requested=f.model, task_class=task_class)
    if local_minutes is None:
        lt = time.localtime()
        local_minutes = lt.tm_hour * 60 + lt.tm_min
    # 1. resolve
    pin = pin or f.pin
    if pin or f.model in cfg.deployments:
        d = cfg.deployments.get(pin or f.model)
        if d is None:
            t.strict = True
            return Decision.err(404, "hydra_unknown_deployment", t, f"no deployment {pin!r}")
        return _strict(cfg, state, f, d, t, now)
    route = cfg.route_by_id_or_alias.get(f.model)
    if route is None:
        if cfg.settings.unknown_model == "404":
            return Decision.err(404, "hydra_unknown_model", t, f"unknown model {f.model!r}")
        route = cfg.routes[cfg.default_route]
        t.note(f"unknown id {f.model!r} -> default_route {route.id}")
    requested = {f.model, route.id}
    origin = route           # its same_family anchor survives a rule hop (a rule never escapes it)
    # 2. rules: first setter wins per key; device/role only with a trusted caller
    eff: dict[str, tuple[Any, str]] = {}
    for r in cfg.rules:
        if _matches(r.when, f, caller, local_minutes, requested, task_class):
            for k, v in r.then.items():
                eff.setdefault(k, (v, r.name))
            t.rules.append(r.name)
    if "route" in eff:
        new = cfg.routes[eff["route"][0]]
        if new.id != route.id:
            t.note(f"rule {eff['route'][1]}: route {route.id} -> {new.id}")
        route = new                                                  # one hop, never chained
    t.route = route.id
    need = set(route.require) | set(_strlist(eff.get("require", ((), ""))[0])) | implied_caps(f)
    prefer = set(_strlist(eff.get("prefer", ((), ""))[0]))
    eff_nodes = {k: (set(_strlist(v[0])), v[1]) for k, v in eff.items() if k in ("only_nodes", "ignore_nodes")}
    # 3. candidates + filters, every exclusion with its reason
    cands: list[Candidate] = []
    ctx_only: list[Candidate] = []
    carried = origin if origin is not route and origin.same_family else None
    for tg in route.targets:
        d = cfg.deployments[tg.d]
        n = cfg.nodes[d.node]
        prio = -1 if tg.d in prefer else tg.priority
        why = policy_reason(cfg, d, carried) if carried is not None else None
        if why:
            t.excluded[tg.d] = why
            continue
        why = _exclude(d, n, state, f, need, route, eff_nodes, now, cfg)
        if why:
            t.excluded[tg.d] = why
            if why.startswith("ctx ") and "min_ctx" not in why and \
                    _exclude(d, n, state, f, need, route, eff_nodes, now, cfg, check_ctx=False) is None:
                ctx_only.append(Candidate(d, n, prio, tg.weight))
        else:
            cands.append(Candidate(d, n, prio, tg.weight))
    # The family policy already ran inside the loop, so ctx_only holds only
    # policy-clean targets: the last resort below can never pick a forbidden
    # family, nor 503 because the largest context happened to be one.
    if not cands and ctx_only:
        # Every otherwise-routable target was excluded ONLY by the (deliberately
        # conservative) token estimate. Send it to the largest context anyway:
        # either it fits (the estimate over-counted) or the engine answers its
        # own overflow 400, which passes through verbatim so runner.py can
        # compact. A hydra 503 here would break compaction.
        best = max(ctx_only, key=lambda c: c.d.ctx)
        t.note(f"ctx estimate excluded every target; last resort -> {best.d.id} "
               "(engine decides, overflow 400 passes through)")
        cands = [best]
    if not cands:
        detail = "; ".join(f"{k}: {v}" for k, v in t.excluded.items()) or "no targets"
        return Decision.err(503, "hydra_unavailable", t,
                            f"no routable deployment for route {route.id} ({detail})", retry_after=5)
    # 4. select
    def node_load(c: Candidate) -> int:
        return state.node_inflight(c.n.id)

    def load(c: Candidate) -> float:
        return (node_load(c) + 1) / c.n.slots / c.weight

    def unsat(c: Candidate) -> bool:
        return node_load(c) < c.n.slots

    tie = {id(c): state.rng.random() for c in cands}
    groups = sorted({c.prio for c in cands})
    sticky = state.affinity.get(f.session_key, now) if route.affinity != "none" else None
    ordered: list[Candidate] = []
    if route.affinity == "sticky" and sticky:
        c = next((c for c in cands if c.d.id == sticky and unsat(c)), None)
        if c:
            ordered.append(c)
            t.note(f"affinity: sticky hit {c.d.id}")
    chosen = None
    for g in groups:
        live = [c for c in cands if c.prio == g]
        free = [c for c in live if unsat(c)]
        if free:
            chosen = g
            aff = None
            if route.affinity == "session" and sticky:
                aff = next((c for c in free if c.d.id == sticky), None)
                if aff:
                    t.note(f"affinity: session hit {aff.d.id}")
            ordered += ([aff] if aff else []) + sorted(free, key=lambda c: (load(c), tie[id(c)]))
            if g != groups[0]:
                t.note(f"spill: priority group {groups[0]} saturated -> group {g}")
            break
        if not route.spill:
            chosen = g
            ordered += sorted(live, key=lambda c: (load(c), tie[id(c)]))
            t.note(f"group {g} saturated, spill=false -> engine queue")
            break
    if chosen is None:
        ordered += sorted([c for c in cands if c.prio == groups[0]], key=lambda c: (load(c), tie[id(c)]))
        t.note("all saturated -> engine queue on the preferred group")
    rest = sorted([c for c in cands if all(c is not o for o in ordered)],
                  key=lambda c: (c.prio, load(c), tie[id(c)]))
    plan: list[Candidate] = []
    for c in ordered + rest:
        if all(c.d.id != p.d.id for p in plan):
            plan.append(c)
    plan = plan[: route.max_attempts]
    return Decision(status=200, route=route.id, attempts=plan, trace=t)


def instinct_worthwhile(cfg: Config, state: FleetState, f: Features, caller: Caller, now: float,
                        local_minutes: int | None = None) -> bool:
    """Should hydra ask instinct at all? (instinct plan §5.11, hydra obligation 2)

    Only for a non-strict request, only when some `when.task_class` rule would
    otherwise match, and only when at least two eligible deployments remain
    across the static decision and the routes those rules could send it to.
    Mechanical facts (caps, ctx, health) are applied first and never delegated.
    """
    if not cfg.uses_task_class():
        return False
    if f.pin or f.model in cfg.deployments:
        return False
    if local_minutes is None:
        lt = time.localtime()
        local_minutes = lt.tm_hour * 60 + lt.tm_min
    base = decide(cfg, state, f, caller, now, local_minutes=local_minutes)
    route = cfg.route_by_id_or_alias.get(f.model) or cfg.routes.get(cfg.default_route)
    if route is None:
        return False
    requested = {f.model, route.id}
    eligible = {c.d.id for c in base.attempts} if base.ok else set()
    any_rule = False
    for r in cfg.rules:
        if "task_class" not in r.when:
            continue
        others = {k: v for k, v in r.when.items() if k != "task_class"}
        if not _matches(others, f, caller, local_minutes, requested, None):
            continue
        any_rule = True
        labels = _strlist(r.when["task_class"])
        if labels:
            alt = decide(cfg, state, f, caller, now, local_minutes=local_minutes, task_class=labels[0])
            if alt.ok:
                eligible |= {c.d.id for c in alt.attempts}
    return any_rule and len(eligible) >= 2


# ───────────────────────────────── body + wire helpers ─────────────────────────────────

def forward_body(body: dict, d: Deployment, n: Node) -> tuple[dict, list[str]]:
    """The ONLY edits hydra makes to a request body (plan §6.6): `model`, and an
    `id_slot` the target cannot honour. Everything else passes untouched.

    The range check needs a slot count somebody stated. On the implicit node
    with INFERENCE_SLOTS unset (`slots_known` False) the 1 is a guess, and
    stripping on it silently dropped beast-gate's per-device slot affinity on
    every `-np N` rig; the engine is then the judge, as without hydra."""
    out = dict(body)
    edits = []
    if out.get("model") != d.upstream:
        out["model"] = d.upstream
        edits.append("model")
    if "id_slot" in out:
        v = out["id_slot"]
        ok = (n.engine == "llama" and "id_slot" in d.caps and isinstance(v, int)
              and not isinstance(v, bool) and v >= 0
              and (v < n.slots or not n.slots_known))
        if not ok:
            del out["id_slot"]
            edits.append("id_slot")
    return out, edits


def sse_error_event(node: str, deployment: str, request_id: str) -> bytes:
    """The mid-stream failure event (plan §6.6). Sent WITHOUT a following [DONE]."""
    doc = {"error": {"message": f"upstream {node} failed mid-stream", "type": "hydra_upstream_error",
                     "code": "upstream_failed_midstream", "hydra_deployment": deployment,
                     "request_id": request_id}}
    return b"data: " + json.dumps(doc, separators=(",", ":")).encode() + b"\n\n"


def is_model_404(status: int, text: str) -> bool:
    return status == 404 and "model" in (text or "").lower()
