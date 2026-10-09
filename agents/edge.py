#!/usr/bin/env python3
"""beast-gate — the identity-aware inference edge for OpenBeast.

WHY THIS EXISTS. llama-server has zero tenancy primitives (verified against
the vendored source): no users, a flat shared API key, opportunistic slot
selection, an unbounded task queue, and no per-caller accounting. Meanwhile
`tailscale serve --https=8443 -> 127.0.0.1:8080` publishes its ENTIRE route
table to the tailnet — /slots and /props metadata, POST /lora-adapters (global
model mutation), GET/DELETE /v1/stream/<conv_id> (read or cancel someone
else's generation). Every remote client therefore collapsed into one
anonymous caller with the run of the box.

beast-gate is the missing hop: the one place on the INFERENCE path where
identity exists. Point `tailscale serve --https=8443` at this instead of
llama-server and you get, per device:

  * per-device bearer keys, hot-reloaded from .run/clients.json — revoke a
    lost laptop without restarting llama-server (which would destroy the
    operator's KV cache and every live stream)
  * a strict path allowlist — the OpenAI surface only; /lora-adapters,
    /slots, /v1/stream, /infill and friends stop existing for remote callers
  * `id_slot` stripped from client bodies (it is unauthenticated, wraps
    modulo the slot count onto another tenant's slot, and jumps the deferred
    queue) and re-injected ONLY from the server-side device->slot map
  * X-Conversation-Id namespaced per device, so two devices cannot collide
    on — or hijack — each other's stream sessions
  * a token bucket + max-in-flight cap per device: llama-server's queue is an
    unbounded deque with no admission control, so backpressure must live here
  * one audit line per completion, with token usage, and Prometheus /metrics

WHAT IT DELIBERATELY IS NOT. It is not in front of the local command center:
Open WebUI keeps talking to llama-server (or the agent router) on loopback
exactly as before. Enabling the gate changes what the TAILNET sees, not what
the rig does. Off by default.

Env (resolved from openbeast.conf by scripts/lib/conf.sh):
  OPENBEAST_EDGE_PORT          listen port (default 8090)
  OPENBEAST_BIND               bind host (default 127.0.0.1 — keep it loopback
                               and publish via tailscale serve)
  OPENBEAST_LLAMA_UPSTREAM     real llama-server (default http://127.0.0.1:8080)
  OPENBEAST_API_KEY            upstream key, if llama-server runs --api-key
  OPENBEAST_EDGE_RATE_LIMIT    requests/minute per device (default 120)
  OPENBEAST_EDGE_MAX_INFLIGHT  concurrent generations per device (default 2);
                               a prompt array or n>1 counts prompts x n
  OPENBEAST_EDGE_ALLOW_ANON    "true" = while there is NO registry file, serve
                               every caller as the "anon" device (default
                               false = fail closed). Ignored once
                               .run/clients.json exists — even emptied by
                               `clients.sh remove`, even unreadable: then
                               no/unknown key -> 401
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import socket
import sys
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from hydra_caller import HEADER as _HYDRA_CALLER_HEADER
from hydra_caller import CallerToken

REPO_DIR = os.environ.get("OPENBEAST_REPO_DIR") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
RUN_DIR = os.path.join(REPO_DIR, ".run")
REGISTRY_PATH = os.path.join(RUN_DIR, "clients.json")
AUDIT_PATH = os.path.join(RUN_DIR, "inference-audit.jsonl")
# Gate-owned last-seen sidecar. Kept OUT of clients.json so the data path
# never rewrites the authorization source (see Registry.touch).
_LASTSEEN_PATH = os.path.join(RUN_DIR, "clients-lastseen.json")
_TOUCH_INTERVAL_S = 60.0
# Proof-of-locality for the /gate/* introspection routes (see _local_token).
_LOCAL_TOKEN_PATH = os.path.join(RUN_DIR, "edge-local.token")
_LOCAL_TOKEN: str | None = None
# Requests larger than this are refused rather than buffered whole in RAM.
MAX_BODY_BYTES = int(os.environ.get("OPENBEAST_EDGE_MAX_BODY", str(32 * 1024 * 1024)))

PORT = int(os.environ.get("OPENBEAST_EDGE_PORT", "8090"))
BIND = os.environ.get("OPENBEAST_BIND", "127.0.0.1").strip() or "127.0.0.1"
UPSTREAM = os.environ.get(
    "OPENBEAST_LLAMA_UPSTREAM", "http://127.0.0.1:8080").rstrip("/")
_UPSTREAM_KEY = os.environ.get("OPENBEAST_API_KEY", "").strip()
RATE_LIMIT = int(os.environ.get("OPENBEAST_EDGE_RATE_LIMIT", "120"))
MAX_INFLIGHT = int(os.environ.get("OPENBEAST_EDGE_MAX_INFLIGHT", "2"))
ALLOW_ANON = os.environ.get(
    "OPENBEAST_EDGE_ALLOW_ANON", "false").strip().lower() == "true"

# The ONLY paths a remote client may reach. Everything else 404s — a remote
# caller should not be able to tell which of the many llama-server routes
# exist. Keep this list minimal and additive-only; each entry is a decision.
ALLOWED_PATHS = frozenset({
    "/health",
    "/v1/models",
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/embeddings",
})

# The allowlisted endpoints whose body is a JSON object the gate MUST read to
# enforce tenancy (id_slot strip + server-side slot, include_usage). A body it
# cannot parse here is refused, never forwarded — see _sanitize_body.
JSON_PATHS = frozenset({
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/embeddings",
})
# No legitimate request nests anywhere near this (a chat body with a deep tool
# schema is ~15-20). The cap must sit far below the ~50k where Python's json
# gives up AND llama-server's nlohmann deep copy overflows its thread stack.
MAX_JSON_DEPTH = 64

# Hop-by-hop headers must not be relayed (same list as agents/router.py).
# Authorization is deliberately ABSENT from the strip list on the RESPONSE
# path but is replaced on the REQUEST path: the device's key never reaches
# llama-server; we substitute the upstream key (or nothing).
_HOP_BY_HOP = {"host", "content-length", "transfer-encoding", "connection"}

# Identity headers a REMOTE client must never be able to assert for itself.
# The agent router gates its spawn path on X-OpenWebUI-User-Role, so letting
# a device set it would be privilege escalation by header.
# x-hydra-caller: the token that makes beast-hydra trust the two above — a
# device must never be able to present one of its own.
_CLIENT_SPOOFABLE = {
    "x-openwebui-user-role", "x-openwebui-user-id", "x-openwebui-user-name",
    "x-openwebui-user-email", "x-openbeast-device", "x-hydra-caller",
}

# beast-hydra (HYDRA=true): the gate vouches for the device it authenticated
# with X-Hydra-Caller, and hands hydra its request id so the two audits join.
# Unconfigured (OPENBEAST_HYDRA_CALLER_TOKEN_FILE unset — every stack without
# hydra) the upstream headers are exactly what they always were.
_HYDRA_CALLER = CallerToken()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Device registry — hot-reloaded so `clients.sh revoke` takes effect without
# a restart (restarting llama-server would evict every live conversation).
# ---------------------------------------------------------------------------

class Registry:
    def __init__(self, path: str = REGISTRY_PATH):
        self.path = path
        self._mtime = None
        self._by_hash: dict[str, dict] = {}
        self._present = False
        # The FILE is there, parsed or not. Distinct from _present (a map was
        # loaded from it) — see `exists`.
        self._exists = False
        self._bad_stamp = None
        self._touched: dict[str, float] = {}
        self.reload()

    def reload(self) -> None:
        try:
            st = os.stat(self.path)
        except OSError:
            self._by_hash, self._present, self._mtime = {}, False, 0.0
            self._exists = False
            self._size = -1
            return
        self._exists = True
        # mtime alone is not enough: two writes inside the same filesystem
        # timestamp tick (mtime granularity can be 1s) would leave a stale
        # map — and a stale map means a MISSED REVOCATION. Key the cache on
        # (mtime, size, inode) so same-second edits are still noticed.
        stamp = (st.st_mtime, st.st_size, st.st_ino)
        if stamp == self._mtime:
            return
        try:
            with open(self.path) as f:
                data = json.load(f)
            # The wrong SHAPE is as unusable as a parse error (and used to
            # be an AttributeError out of every request).
            devices = data.get("devices", [])
            by_hash = {}
            for dev in devices:
                key_hash = (dev.get("key_sha256") or "").strip().lower()
                if key_hash:
                    by_hash[key_hash] = dev
        except (OSError, ValueError, AttributeError, TypeError) as e:
            # A half-written or corrupt registry must NOT silently open the
            # door: keep serving the last good map. On a FIRST load there is
            # no last good map — `exists` is what keeps that closed. Said
            # once per bad file, not once per request.
            if stamp != self._bad_stamp:
                self._bad_stamp = stamp
                _log(f"ERROR: {self.path} exists but cannot be read as a "
                     f"device registry ({type(e).__name__}) — "
                     + ("serving the last good copy" if self._present else
                        "refusing every caller")
                     + " until it is repaired: ./scripts/clients.sh list "
                     "names the fault; restore the file or move it aside "
                     "and re-enroll")
            return
        self._by_hash = by_hash
        self._present = True
        self._mtime = stamp

    @property
    def configured(self) -> bool:
        """True when a registry exists with at least one device.

        Reloads first — deliberately. Without this, a gate that started before
        the first device was enrolled would never notice the registry
        appearing: `configured` stays False, so `lookup()` (the only other
        reload caller) is unreachable, and every request 401s until a
        restart. That would break both the hot-reload contract and start.sh's
        own printed "enroll a device" instructions. reload() is stat-gated,
        so the steady-state cost is one os.stat.
        """
        self.reload()
        return self._present and bool(self._by_hash)

    @property
    def exists(self) -> bool:
        """True when the registry FILE is there — empty or unreadable too.

        This, not `configured`, decides whether EDGE_ALLOW_ANON may apply.
        Enrollment having happened at all is the operator's statement that
        callers are identified, and two states used to read as "no registry"
        and reopen the gate to every tailnet peer: `clients.sh remove` of the
        last device (which re-admitted the very key it removed), and a file
        truncated before a gate restart (no last good map to keep).
        """
        self.reload()
        return self._exists

    def lookup(self, presented_key: str) -> dict | None:
        """Device for a bearer key, or None. Revoked devices return None."""
        self.reload()
        digest = hashlib.sha256(presented_key.encode()).hexdigest()
        # compare_digest against every candidate: constant-time per entry, and
        # a dict hit on a hex digest is not itself a secret-dependent branch.
        for key_hash, dev in self._by_hash.items():
            if hmac.compare_digest(key_hash, digest):
                return None if dev.get("revoked_at") else dev
        return None

    def touch(self, device_id: str) -> None:
        """Record last-seen in a GATE-OWNED sidecar, throttled.

        Deliberately does NOT write clients.json. Two reasons, both learned
        the hard way:
          1. SAFETY — an unlocked read-modify-write of the registry races
             `clients.sh revoke`: a touch that read the file before the
             revoke and wrote after it silently RESURRECTS a revoked device.
             Never let the data path rewrite the authorization source.
          2. WEAR + LATENCY — the old version rewrote the whole registry on
             every single request, synchronously, on the event loop. That is
             gratuitous disk churn (see the SSD-wear item in docs/TODO.md)
             and it blocked concurrent streams.
        `clients.sh list/show` merges this file for display.
        """
        now = time.monotonic()
        last = self._touched.get(device_id)
        # `last is None` means never touched -> always write. Do NOT use 0.0 as
        # the sentinel: time.monotonic() is time since BOOT, so on a machine
        # that booted less than _TOUCH_INTERVAL_S ago the arithmetic would
        # silently skip a device's very first last-seen record.
        if last is not None and now - last < _TOUCH_INTERVAL_S:
            return
        self._touched[device_id] = now
        try:
            seen = {}
            try:
                with open(_LASTSEEN_PATH) as f:
                    seen = json.load(f) or {}
            except (OSError, ValueError):
                seen = {}
            seen[device_id] = _now()
            fd, tmp = tempfile.mkstemp(prefix=".lastseen-", suffix=".json",
                                       dir=os.path.dirname(_LASTSEEN_PATH))
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump(seen, f)
                os.replace(tmp, _LASTSEEN_PATH)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Per-device admission control. llama-server's queue is unbounded with no
# per-request timeout, so a single remote agent loop can monopolize the rig.
# ---------------------------------------------------------------------------

class Bucket:
    """Token bucket + in-flight cap, per device."""

    def __init__(self, per_min: int, max_inflight: int):
        self.retune(per_min)
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self.inflight = 0
        self.max_inflight = max(1, max_inflight)

    def retune(self, per_min: int) -> None:
        """Adopt a new per-minute limit without dropping in-flight state."""
        self.capacity = max(1, int(per_min))
        self.rate = self.capacity / 60.0
        # Never let a lowered limit leave a device holding more credit than
        # the new ceiling allows.
        if getattr(self, "tokens", 0.0) > self.capacity:
            self.tokens = float(self.capacity)

    def reserve(self) -> str | None:
        """Atomically admit one request, or return a refusal reason.

        Check-and-increment MUST happen with no await in between: the caller
        reads the request body (a real suspension point) before proxying, and
        a check-then-act split there let a device blow straight through
        EDGE_MAX_INFLIGHT with concurrent requests.
        """
        now = time.monotonic()
        self.tokens = min(self.capacity,
                          self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens < 1.0:
            return "rate_limited"
        if self.inflight >= self.max_inflight:
            return "max_inflight"
        self.tokens -= 1.0
        self.inflight += 1
        return None

    def reserve_more(self, extra: int) -> str | None:
        """Atomically widen an admitted request to `extra` more generations.

        A request that fans out (prompt arrays, n) holds one in-flight unit
        per generation it creates upstream, and pays one rate token each —
        otherwise EDGE_MAX_INFLIGHT would bound HTTP requests, not work.
        Same no-await rule as reserve(). Refuses without taking anything.
        """
        if extra <= 0:
            return None
        now = time.monotonic()
        self.tokens = min(self.capacity,
                          self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens < extra:
            return "rate_limited"
        if self.inflight + extra > self.max_inflight:
            return "max_inflight"
        self.tokens -= extra
        self.inflight += extra
        return None

    def release(self, n: int = 1) -> None:
        # Guard against a double-release wedging the counter negative.
        self.inflight = max(0, self.inflight - n)


class Limiter:
    def __init__(self):
        self._buckets: dict[str, Bucket] = {}

    def bucket(self, device: dict) -> Bucket:
        # Keyed on the ENROLLMENT, not the reusable id: a removed-and-
        # re-enrolled device must start with fresh counters rather than
        # inheriting the previous holder's rate/in-flight state.
        did = device_uid(device)
        per_min = int(device.get("rate_limit_per_min") or RATE_LIMIT)
        b = self._buckets.get(did)
        if b is None:
            b = Bucket(per_min, MAX_INFLIGHT)
            self._buckets[did] = b
        elif b.capacity != max(1, per_min):
            # `clients.sh` changed this device's limit — adopt it live rather
            # than caching the value seen at first contact for process life.
            b.retune(per_min)
        return b


# ---------------------------------------------------------------------------
# Audit + metrics. Mirrors agents/openapi_tools.py's discipline: record WHAT
# happened and how much it cost, never the content and never key material.
# ---------------------------------------------------------------------------

_METRICS = {
    "requests_total": {},     # (device, path, outcome) -> count
    "denied_total": {},       # reason -> count
    "prompt_tokens": {},      # device -> count
    "completion_tokens": {},  # device -> count
    "latency_ms": {},         # device -> cumulative
}


def device_uid(device: dict) -> str:
    """Stable identity for ONE enrollment of a device.

    The human-facing `id` is reusable: `clients.sh remove laptop-air` then
    `enroll laptop-air` yields a different physical device wearing the same
    name, which would silently conflate two devices in the audit trail (and
    inherit the old one's in-flight counters). Binding the enrollment
    timestamp in makes each enrollment distinct without adding a field the
    CLI would have to write.
    """
    raw = f"{device.get('id','anon')}:{device.get('enrolled_at','')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _audit(device: str, user: str | None, path: str, status: int,
           usage: dict | None, ms: int, model: str | None,
           outcome: str, request_id: str = "", uid: str = "") -> None:
    row = {
        "ts": _now(),
        "request_id": request_id,
        "device": device,
        # Distinguishes re-enrollments of the same device id; join on this,
        # not `device`, when attributing historical usage.
        "device_uid": uid,
        # CLAIMED by the caller, not authenticated — see docs/BEAST_SLOT.md.
        "user_claimed": user,
        "path": path,
        "model": model,
        "status": status,
        "outcome": outcome,
        "duration_ms": ms,
        "prompt_tokens": (usage or {}).get("prompt_tokens"),
        "completion_tokens": (usage or {}).get("completion_tokens"),
    }
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        fd = os.open(AUDIT_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass
    _bump("requests_total", (device, path, outcome))
    if usage:
        _add("prompt_tokens", device, usage.get("prompt_tokens") or 0)
        _add("completion_tokens", device, usage.get("completion_tokens") or 0)
    _add("latency_ms", device, ms)


def _bump(metric: str, key) -> None:
    _METRICS[metric][key] = _METRICS[metric].get(key, 0) + 1


def _add(metric: str, key, n: int) -> None:
    _METRICS[metric][key] = _METRICS[metric].get(key, 0) + n


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# Request handling
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    """Operator-visible line (stdout -> .run/stack.log in daemon mode).

    The gate is a security boundary; an admin must be able to answer "why did
    that device get 401/429" without a debugger. Never log key material.
    """
    print(f"[beast-gate] {_now()} {msg}", flush=True)


# Unauthenticated denials are kept out of the audit FILE so a keyless peer
# cannot grow it — and the same logic applies to stack.log, which every
# denial line lands in. Per reason (a small fixed set, so bounded memory; a
# per-key budget would not be, since the caller picks the key), log the first
# few lines of each window and summarize the rest. /gate/metrics keeps the
# exact count.
_DENY_LOG_BURST = 10
_DENY_LOG_WINDOW_S = 60.0
_deny_log: dict[str, list] = {}      # reason -> [window_start, logged, suppressed]
_clock = time.monotonic              # indirection so tests can drive the window


def _log_denial(reason: str, msg: str) -> None:
    now = _clock()
    w = _deny_log.get(reason)
    if w is None or now - w[0] >= _DENY_LOG_WINDOW_S:
        if w is not None and w[2]:
            _log(f"suppressed {w[2]} more 'denied {reason}' lines in the "
                 f"last {int(now - w[0])}s (exact count in /gate/metrics)")
        w = [now, 0, 0]
        _deny_log[reason] = w
    if w[1] < _DENY_LOG_BURST:
        w[1] += 1
        _log(msg)
    else:
        w[2] += 1


def _key_fp(key: str) -> str:
    """Short, non-reversible fingerprint of a presented key, for logs only —
    enough to tell 'revoked laptop still retrying' from 'someone guessing'."""
    if not key:
        return "none"
    return hashlib.sha256(key.encode()).hexdigest()[:8]


def _peer(request: Request) -> str:
    try:
        return request.client.host if request.client else "?"
    except Exception:
        return "?"


def _local_token() -> str:
    """Shared secret proving a caller is ON this box, minted per gate start.

    The transport peer address CANNOT be used for this: `tailscale serve`
    reverse-proxies into 127.0.0.1, so every REMOTE tailnet caller arrives
    looking exactly like loopback. An earlier version trusted
    request.client.host and therefore protected nothing in the deployment we
    actually document.

    Filesystem access is the real local/remote boundary here — a tailnet peer
    cannot read this file, and the rig's own tooling can. Written 0600 at
    startup; regenerated every run so a stale copy is worthless.
    """
    global _LOCAL_TOKEN
    if _LOCAL_TOKEN is None:
        _LOCAL_TOKEN = uuid.uuid4().hex
        try:
            os.makedirs(RUN_DIR, exist_ok=True)
            # O_TRUNC on an existing path keeps the OLD mode, so fchmod
            # explicitly — a token left 0644 by some earlier run would be
            # readable by any local user.
            fd = os.open(_LOCAL_TOKEN_PATH,
                         os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(_LOCAL_TOKEN)
        except Exception as e:
            # Do NOT swallow this. If the file cannot be written, no local
            # tool can ever present the token, so doctor/healthcheck lose
            # introspection with no clue why. Say so loudly and keep the
            # in-memory token (a device key still works).
            _log(f"ERROR: could not write {_LOCAL_TOKEN_PATH} ({e}) — "
                 "rig-local introspection (doctor/healthcheck detail) will be "
                 "refused; an enrolled device key still works")
    return _LOCAL_TOKEN


def _is_local(request: Request) -> bool:
    """True only for callers that proved filesystem access to this box."""
    presented = request.headers.get("x-openbeast-local", "")
    if not presented:
        return False
    # Compare BYTES. compare_digest on str raises TypeError for any non-ASCII
    # character, and Starlette decodes header bytes as latin-1 — so a single
    # 0x80-0xFF byte from an unauthenticated caller would turn this check into
    # an unhandled 500 and spray a traceback into .run/stack.log on every
    # request. Encoding first makes a hostile header simply not match.
    return hmac.compare_digest(presented.encode("utf-8", "surrogateescape"),
                               _local_token().encode())


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _identify(request: Request, registry: Registry) -> tuple[dict | None, str]:
    """(device, reason). device None => refuse with `reason`."""
    key = _bearer(request)
    if registry.configured:
        if not key:
            return None, "no_key"
        dev = registry.lookup(key)
        if dev is None:
            return None, "bad_key"
        return dev, "ok"
    # No device to match. Fail CLOSED unless explicitly opted out — an empty
    # registry must not mean "everyone is welcome" (the RBAC fail-closed
    # lesson from 2026-07-17). The opt-out covers ONLY a rig that has never
    # had a registry file: see Registry.exists.
    if registry.exists:
        return None, ("no_registry" if registry._present
                      else "registry_unreadable")
    if ALLOW_ANON:
        return {"id": "anon", "label": "unregistered"}, "ok"
    return None, "no_registry"


def _auth_mode(registry: Registry) -> str:
    """devices | anon | closed — what _identify does with a caller right now."""
    if registry.configured:
        return "devices"
    return "anon" if ALLOW_ANON and not registry.exists else "closed"


# A JSON string token (escapes included) or one structural bracket. Strings
# are matched whole so a "[" inside a string never counts as nesting. The
# unrolled string form never backtracks, and an UNTERMINATED string runs to
# end-of-input (`\\?\Z`) instead of failing: a failed match made finditer
# retry from the next quote and rescan the tail, so a body of repeated '"\\'
# cost O(n^2) — 128 KB froze the gate's event loop for 24 s.
_JSON_TOKEN = re.compile(rb'"[^"\\]*(?:\\.[^"\\]*)*(?:"|\\?\Z)|[\[\]{}]',
                         re.DOTALL)


def _json_too_deep(raw: bytes, limit: int = MAX_JSON_DEPTH) -> bool:
    """True when the body nests brackets deeper than `limit`.

    Iterative and run BEFORE json.loads, which recurses: at ~50k levels it
    raises RecursionError, and the old code then forwarded the ORIGINAL bytes
    — client id_slot intact, no include_usage — to a llama-server whose
    parser has no depth limit and whose deep copy of that value overflows
    the stack and kills the model server for every tenant. Stops at the
    first bracket past the cap, so a hostile body costs O(limit) to refuse.
    A malformed body can only miscount here; json.loads still rejects it.
    """
    depth = 0
    for m in _JSON_TOKEN.finditer(raw):
        c = m.group()
        if c in (b"[", b"{"):
            depth += 1
            if depth > limit:
                return True
        elif c in (b"]", b"}"):
            depth -= 1
    return False


class BadBody(ValueError):
    """A JSON-endpoint body the gate refuses to forward (-> 400)."""


def _generations(body: dict, path: str) -> int:
    """How many upstream generations this ONE request becomes.

    llama-server turns a /v1/completions `prompt` (or /v1/embeddings `input`)
    ARRAY into one task per element, with no bound, and adds n_cmpl-1 child
    tasks per prompt (`n` is its alias). Counting the HTTP request as one
    let a single admitted request fill every slot and queue thousands of
    tasks ahead of every other tenant. Mirrors tokenize_input_prompts(): an
    array containing any integer is ONE token-list prompt, not many.
    Over-counting is the safe direction, so n_cmpl and n take the max.
    """
    if path == "/v1/completions":
        prompt = body.get("prompt")
    elif path == "/v1/embeddings":
        prompt = body.get("input", body.get("content"))
    else:
        prompt = None                    # chat renders ONE prompt from messages
    inputs = 1
    if isinstance(prompt, list) and not any(
            isinstance(p, int) and not isinstance(p, bool) for p in prompt):
        inputs = max(1, len(prompt))
    n = 1
    if path != "/v1/embeddings":         # embeddings make no child tasks
        for k in ("n_cmpl", "n"):
            v = body.get(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, int) or (isinstance(v, float) and v == v
                                      and abs(v) != float("inf")):
                n = max(n, int(v))
    return inputs * n


def _sanitize_body(raw: bytes, device: dict,
                   path: str = "/v1/chat/completions"
                   ) -> tuple[bytes, str | None, bool, int]:
    """Strip client-controlled tenancy knobs; inject server-side affinity.

    Returns (body, model_name, streaming, generations) — see _generations
    for the last. Raises BadBody for anything that
    is not a JSON object within MAX_JSON_DEPTH. FAIL CLOSED: every tenancy
    and metering guarantee below depends on the gate having parsed the body,
    so a body it cannot parse must never reach llama-server verbatim — that
    was a parser differential that bypassed all of them.
    """
    # Strict UTF-8 FIRST. json.loads(bytes) would auto-detect UTF-16/32, and
    # the byte-level depth scan below assumes an ASCII-compatible encoding:
    # a UTF-16 "∀" (bytes 00 22) is a stray quote that desyncs it, letting a
    # body nested far past the cap through. llama-server only takes UTF-8.
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise BadBody("request body is not UTF-8")
    if text.startswith("\ufeff"):
        text = text[1:]         # UTF-8 BOM: json.loads(bytes) took it
    if _json_too_deep(raw):
        raise BadBody(f"request body nests deeper than {MAX_JSON_DEPTH} levels")
    try:
        body = json.loads(text)
    except (ValueError, RecursionError) as e:
        # ValueError covers JSONDecodeError and Python's int-digit limit;
        # RecursionError is belt-and-braces behind the cap.
        raise BadBody(f"request body is not valid JSON ({type(e).__name__})")
    if not isinstance(body, dict):
        raise BadBody("request body must be a JSON object")
    # id_slot is unauthenticated in llama-server: it wraps modulo the slot
    # count (landing on another tenant's slot) and a pinned task jumps the
    # deferred queue ahead of unpinned callers. Never honor the client's.
    body.pop("id_slot", None)
    slot = device.get("slot")
    if isinstance(slot, bool):
        slot = None                      # JSON true/false is not a slot index
    if isinstance(slot, int):
        body["id_slot"] = slot
    streaming = bool(body.get("stream"))
    if streaming:
        # Without this the streaming path — which is the DEFAULT for chat —
        # never emits a usage block, so every metered token silently reads
        # null and the audit promise is empty in normal use.
        opts = body.get("stream_options")
        opts = dict(opts) if isinstance(opts, dict) else {}
        opts["include_usage"] = True
        body["stream_options"] = opts
    return (json.dumps(body).encode(), body.get("model"), streaming,
            _generations(body, path))


def _upstream_headers(request: Request, device: dict, request_id: str = "") -> dict:
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _HOP_BY_HOP
               and k.lower() not in _CLIENT_SPOOFABLE}
    # Re-assert identity from the AUTHENTICATED device, so a client can't
    # smuggle its own X-OpenWebUI-User-* headers to something downstream that
    # trusts them (the agent router gates its spawn path on exactly that role
    # header).
    headers["X-OpenBeast-Device"] = str(device.get("id", "anon"))
    # The device key is OURS, not llama-server's. Replace it with the
    # upstream key so a device key is never forwarded anywhere.
    headers.pop("authorization", None)
    if _UPSTREAM_KEY:
        headers["Authorization"] = f"Bearer {_UPSTREAM_KEY}"
    # Stream sessions are keyed ONLY by this caller-chosen header upstream, so
    # namespace it per device: two devices can neither collide nor cancel each
    # other's generations by guessing an id.
    conv = headers.pop("x-conversation-id", None) or headers.pop(
        "X-Conversation-Id", None)
    if conv:
        tag = hashlib.sha256(
            f"{device.get('id','anon')}:{conv}".encode()).hexdigest()[:32]
        headers["X-Conversation-Id"] = tag
    if _HYDRA_CALLER.configured:
        # The gate's own id, never a client-chosen one (hydra accepts
        # ^[A-Za-z0-9_-]{1,64}$; ours is 16 hex).
        for k in [k for k in headers if k.lower() == "x-openbeast-request-id"]:
            headers.pop(k)
        if request_id:
            headers["X-OpenBeast-Request-Id"] = request_id
        tok = _HYDRA_CALLER.get()
        if tok:
            headers[_HYDRA_CALLER_HEADER] = tok
    return headers


def _usage_from_sse(tail: bytes) -> dict | None:
    """Pull the usage block out of the final SSE chunks, when present."""
    try:
        for line in tail.decode("utf-8", "replace").splitlines()[::-1]:
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload in ("", "[DONE]"):
                continue
            obj = json.loads(payload)
            if isinstance(obj, dict) and obj.get("usage"):
                return obj["usage"]
    except Exception:
        pass
    return None


def _usage_from_json_tail(tail: bytes) -> dict | None:
    """Usage from the END of a non-streaming JSON reply too big to buffer.

    llama-server serializes `usage` as the LAST top-level key (nlohmann
    objects are key-sorted), so it sits inside the bounded tail even when a
    logprobs-heavy reply is megabytes long. Without this every reply over
    the whole-body buffer was audited with null tokens while the client got
    its usage. A quote inside a string value is escaped, so a `"usage"`
    byte run is a real key; take the last one that decodes to an object.
    """
    dec = json.JSONDecoder()
    text = tail.decode("utf-8", "replace")
    end = len(text)
    while True:
        i = text.rfind('"usage"', 0, end)
        if i < 0:
            return None
        end = i
        j = i + len('"usage"')
        while j < len(text) and text[j] in " \t\r\n":
            j += 1
        if j >= len(text) or text[j] != ":":
            continue
        j += 1
        while j < len(text) and text[j] in " \t\r\n":
            j += 1
        try:
            obj, _ = dec.raw_decode(text, j)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj


async def gate(request: Request):
    started = time.monotonic()
    path = request.url.path.rstrip("/") or "/"
    app = request.app
    registry: Registry = app.state.registry
    client: httpx.AsyncClient = app.state.client

    if path not in ALLOWED_PATHS:
        # 404, not 403: a remote caller learns nothing about what exists.
        _bump("denied_total", "path_not_allowed")
        return JSONResponse(
            {"error": {"message": "not found", "type": "invalid_request_error"}},
            status_code=404)

    device, reason = _identify(request, registry)
    if device is None:
        # Counted in /gate/metrics, with the presented key's short fingerprint
        # in the log so an admin can tell "revoked laptop still retrying" from
        # "someone is guessing". NOT written to the audit FILE: unauthenticated
        # callers must not be able to grow it without bound.
        _bump("denied_total", reason)
        _log_denial(reason, f"denied {reason} path={path} "
                    f"key_fp={_key_fp(_bearer(request))} peer={_peer(request)}")
        hint = ("this rig has no enrolled devices yet — run "
                "./scripts/clients.sh enroll <id> on the rig"
                if reason == "no_registry" else
                "the rig's device registry is unreadable — on the rig, run "
                "./scripts/clients.sh list and repair .run/clients.json"
                if reason == "registry_unreadable" else
                "present a device key: Authorization: Bearer <key>")
        return JSONResponse(
            {"error": {"message": f"unauthorized ({reason}): {hint}",
                       "type": "invalid_request_error"}}, status_code=401)

    device_id = device.get("id", "anon")
    uid = device_uid(device)
    # Correlation handle: returned to the caller as X-OpenBeast-Request-Id and
    # written to every audit row, so a support question ("what happened to my
    # 11:04 request?") is answerable without guessing from timestamps.
    request_id = uuid.uuid4().hex[:16]
    # CLAIMED, not authenticated: this header is client-supplied and a device
    # holder can set it to anything. `device` is the authenticated identity;
    # this is a hint for correlating with WebUI users. Bounded so it can't be
    # used to bloat audit rows.
    user = (request.headers.get("x-openwebui-user-id") or "")[:128] or None

    if path == "/health":
        # Cheap liveness, no admission control — clients poll it.
        try:
            r = await client.get(f"{UPSTREAM}/health", timeout=5)
            return JSONResponse(json.loads(r.text) if r.text.startswith("{")
                                else {"status": "ok"}, status_code=r.status_code)
        except httpx.HTTPError as e:
            return JSONResponse({"error": {"message": f"upstream: {e}"}},
                                status_code=502)

    # Atomic admit: check-and-increment in one step with no await between,
    # otherwise concurrent requests sail past EDGE_MAX_INFLIGHT.
    bucket = app.state.limiter.bucket(device)
    refusal = bucket.reserve()
    if refusal:
        _bump("denied_total", refusal)
        _audit(device_id, user, path, 429, None,
               int((time.monotonic() - started) * 1000), None, refusal,
               request_id, uid)
        retry = "5" if refusal == "rate_limited" else "2"
        msg = ("rate limit exceeded for this device" if refusal == "rate_limited"
               else f"device already has {bucket.max_inflight} generations in flight")
        return JSONResponse(
            {"error": {"message": msg, "type": "rate_limit_error"}},
            status_code=429, headers={"Retry-After": retry})

    # From here the slot is HELD: every exit path must release it. Note the
    # bare `except:`/BaseException handling below — asyncio.CancelledError is
    # a BaseException, so a client disconnect would otherwise skip the
    # release and wedge the device at 429 forever.
    # `held` grows past 1 only when the body fans out (see _generations).
    released = {"done": False, "held": 1}

    def _release():
        if not released["done"]:
            released["done"] = True
            bucket.release(released["held"])

    try:
        if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
            _release()
            return JSONResponse(
                {"error": {"message": "request body too large",
                           "type": "invalid_request_error"}}, status_code=413)
        # Drain INCREMENTALLY and stop at the cap. `await request.body()`
        # buffers the whole body first, so a chunked request (no
        # Content-Length, so the check above cannot fire) would be fully
        # resident before any size check ran — an unbounded-memory path on a
        # box already holding a 27B model.
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > MAX_BODY_BYTES:
                _release()
                _log(f"body over cap device={device_id} path={path}")
                return JSONResponse(
                    {"error": {"message": "request body too large",
                               "type": "invalid_request_error"}},
                    status_code=413)
        raw = bytes(buf)
        body, model, streaming, gens = raw, None, False, 1
        if raw and path in JSON_PATHS:
            try:
                body, model, streaming, gens = _sanitize_body(raw, device, path)
            except BadBody as e:
                # Authenticated, so it is audited (an identity to attribute);
                # nothing was forwarded, so there is no usage to meter.
                _release()
                _audit(device_id, user, path, 400, None,
                       int((time.monotonic() - started) * 1000), None,
                       "bad_request", request_id, uid)
                _log(f"bad body device={device_id} path={path}: {e}")
                return JSONResponse(
                    {"error": {"message": str(e),
                               "type": "invalid_request_error"}},
                    status_code=400)
        if gens > bucket.max_inflight:
            # Could never be admitted, so 400 rather than a 429 to retry.
            _release()
            _bump("denied_total", "fanout")
            _audit(device_id, user, path, 400, None,
                   int((time.monotonic() - started) * 1000), model,
                   "fanout", request_id, uid)
            return JSONResponse(
                {"error": {"message": (
                    f"request fans out into {gens} generations (prompts x n); "
                    f"this device may run at most {bucket.max_inflight} at "
                    "once — split it into smaller requests"),
                    "type": "invalid_request_error"}}, status_code=400)
        refusal = bucket.reserve_more(gens - 1)
        if refusal:
            _release()
            _bump("denied_total", refusal)
            _audit(device_id, user, path, 429, None,
                   int((time.monotonic() - started) * 1000), model,
                   refusal, request_id, uid)
            return JSONResponse(
                {"error": {"message": (
                    f"request needs {gens} generation slots and this device "
                    "does not have them free right now"),
                    "type": "rate_limit_error"}},
                status_code=429,
                headers={"Retry-After": "5" if refusal == "rate_limited"
                         else "2"})
        released["held"] = gens
        headers = _upstream_headers(request, device, request_id)
        registry.touch(device_id)

        req = client.build_request(request.method, f"{UPSTREAM}{path}",
                                   content=body, headers=headers)
        try:
            resp = await client.send(req, stream=True)
        except (httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            # A CONNECT/pool timeout means we never reached the server — that
            # is "unreachable", not "slow". TimeoutException is their parent,
            # so these must be caught FIRST or a dead upstream would be
            # reported as a read timeout with a raise-the-read-timeout hint.
            _release()
            _audit(device_id, user, path, 502, None,
                   int((time.monotonic() - started) * 1000), model,
                   "upstream_error", request_id, uid)
            _log(f"upstream connect timeout device={device_id} path={path}: {e}")
            return JSONResponse(
                {"error": {"message": f"model server unreachable: {e}",
                           "type": "upstream_unavailable"}}, status_code=502)
        except httpx.TimeoutException as e:
            # Distinct from unreachable: the model server IS there and simply
            # took longer than OPENBEAST_EDGE_READ_TIMEOUT to produce the
            # first byte (a long non-streaming completion can do this). Saying
            # "unreachable" would send an operator hunting the wrong problem.
            _release()
            _audit(device_id, user, path, 504, None,
                   int((time.monotonic() - started) * 1000), model,
                   "upstream_timeout", request_id, uid)
            _log(f"upstream timeout device={device_id} path={path}: {e}")
            return JSONResponse(
                {"error": {"message": (
                    "model server did not respond within the gate's timeout "
                    f"({os.environ.get('OPENBEAST_EDGE_READ_TIMEOUT', '600')}s). "
                    "A long non-streaming completion can exceed it — raise "
                    "OPENBEAST_EDGE_READ_TIMEOUT, or stream the request."),
                    "type": "upstream_timeout"}}, status_code=504)
        except httpx.HTTPError as e:
            _release()
            _audit(device_id, user, path, 502, None,
                   int((time.monotonic() - started) * 1000), model,
                   "upstream_error", request_id, uid)
            _log(f"upstream error device={device_id} path={path}: {e}")
            return JSONResponse(
                {"error": {"message": f"model server unreachable: {e}",
                           "type": "upstream_unavailable"}}, status_code=502)
    except BaseException:
        # Includes CancelledError (client vanished mid-read).
        _release()
        raise

    hdrs = {k: v for k, v in resp.headers.items()
            if k.lower() not in _HOP_BY_HOP}
    hdrs["X-OpenBeast-Request-Id"] = request_id
    state = {"tail": b"", "whole": b"", "oversize": False, "audited": False}

    def _audit_reply(usage, outcome_override=None):
        if state["audited"]:
            return
        state["audited"] = True
        if outcome_override:
            _outcome, _status = outcome_override
        elif resp.status_code < 400:
            _outcome, _status = "ok", resp.status_code
        else:
            _outcome, _status = "upstream_status", resp.status_code
        _audit(device_id, user, path, _status, usage,
               int((time.monotonic() - started) * 1000), model,
               _outcome, request_id, uid)

    async def body_iter():
        timed_out = False
        upstream_failed = False
        completed = False
        try:
            async for chunk in resp.aiter_raw():
                # Bounded tail (usage rides the last SSE chunks) plus, for
                # small non-streaming replies, the whole body.
                state["tail"] = (state["tail"] + chunk)[-16384:]
                if len(state["whole"]) < 262144:
                    state["whole"] += chunk
                else:
                    state["oversize"] = True
                yield chunk
            completed = True
        except httpx.TimeoutException:
            # A read timeout AFTER headers lands HERE, not at client.send().
            # Without this the audit ledger would record the request as a
            # clean "ok" with the upstream's 200 — the one outcome that makes
            # a timeout invisible to whoever reads the audit later.
            timed_out = True
            _log(f"upstream read timeout mid-stream device={device_id} "
                 f"path={path} request_id={request_id}")
            raise
        except httpx.HTTPError:
            # Any other transport fault mid-body (ReadError, a
            # RemoteProtocolError when llama-server dies) is the UPSTREAM's
            # failure — auditing it as client_disconnect blamed the client.
            upstream_failed = True
            _log(f"upstream error mid-stream device={device_id} "
                 f"path={path} request_id={request_id}")
            raise
        finally:
            # RELEASE FIRST. aclose() can raise (and on a client disconnect
            # the CancelledError re-raises at that await), which previously
            # skipped the decrement and leaked the slot permanently.
            _release()
            try:
                await resp.aclose()
            except BaseException:
                pass
            usage = None
            if not state["oversize"]:
                try:
                    obj = json.loads(state["whole"] or b"{}")
                    if isinstance(obj, dict):
                        usage = obj.get("usage")
                except Exception:
                    usage = None
            if usage is None:
                usage = _usage_from_sse(state["tail"])
            if usage is None and state["oversize"]:
                usage = _usage_from_json_tail(state["tail"])
            if timed_out:
                _audit_reply(usage, ("upstream_timeout", 504))
            elif upstream_failed:
                _audit_reply(usage, ("upstream_error", 502))
            elif not completed:
                # The client went away mid-body (CancelledError/GeneratorExit
                # lands here). Recorded as "ok" before, an abort — whose
                # generated tokens never got a usage chunk — was
                # indistinguishable from a clean, metered completion.
                _audit_reply(usage, ("client_disconnect", resp.status_code))
            else:
                _audit_reply(usage)

    # BackgroundTask is a SECOND, idempotent release path. If the client
    # vanishes after upstream headers arrive but before Starlette starts
    # iterating body_iter(), that generator's finally never runs and the
    # device's in-flight slot would leak — wedging it at 429 forever.
    # _release() is guarded, so whichever path runs first wins.
    async def _sweep():
        _release()
        try:
            await resp.aclose()
        except BaseException:
            pass
        # Same gap for the ledger: a body_iter() that never started never
        # audited. No-op when it did (the normal path).
        _audit_reply(None, ("client_disconnect", resp.status_code))

    return StreamingResponse(body_iter(), status_code=resp.status_code,
                             headers=hdrs, background=BackgroundTask(_sweep))


def _introspection_allowed(request: Request) -> bool:
    """Gate the /gate/* routes.

    These expose the device roster size and per-device usage counters. The
    gate is published at the tailnet ROOT (`tailscale serve :8443 -> :8090`
    mounts `/`), so leaving them open would hand every tailnet peer — including
    one whose key was just revoked — the device list and usage telemetry.

    Two ways in: the local-token header (rig tooling, which can read
    .run/edge-local.token) or a valid enrolled device key. Deliberately NOT
    the peer address — tailscale serve proxies from 127.0.0.1, so a peer
    check would treat the entire tailnet as local. See _local_token.
    """
    if _is_local(request):
        return True
    reg: Registry = request.app.state.registry
    key = _bearer(request)
    return bool(key) and reg.lookup(key) is not None


async def metrics(request: Request):
    if not _introspection_allowed(request):
        _bump("denied_total", "introspection_unauthorized")
        return JSONResponse(
            {"error": {"message": "not found", "type": "invalid_request_error"}},
            status_code=404)
    lines = [
        "# HELP openbeast_edge_requests_total Requests through beast-gate.",
        "# TYPE openbeast_edge_requests_total counter",
    ]
    for (dev, path, outcome), n in sorted(
            _METRICS["requests_total"].items(), key=lambda kv: str(kv[0])):
        lines.append(f'openbeast_edge_requests_total{{device="{_esc(dev)}",'
                     f'path="{_esc(path)}",outcome="{_esc(outcome)}"}} {n}')
    lines += ["# HELP openbeast_edge_denied_total Refused requests by reason.",
              "# TYPE openbeast_edge_denied_total counter"]
    for reason, n in sorted(_METRICS["denied_total"].items()):
        lines.append(
            f'openbeast_edge_denied_total{{reason="{_esc(reason)}"}} {n}')
    for metric, name, helptext in (
            ("prompt_tokens", "openbeast_edge_prompt_tokens_total",
             "Prompt tokens billed per device."),
            ("completion_tokens", "openbeast_edge_completion_tokens_total",
             "Completion tokens generated per device."),
            ("latency_ms", "openbeast_edge_latency_ms_total",
             "Cumulative upstream latency per device.")):
        lines += [f"# HELP {name} {helptext}", f"# TYPE {name} counter"]
        for dev, n in sorted(_METRICS[metric].items()):
            lines.append(f'{name}{{device="{_esc(dev)}"}} {n}')
    return StreamingResponse(iter(["\n".join(lines) + "\n"]),
                             media_type="text/plain; version=0.0.4")


async def health(request: Request):
    reg: Registry = request.app.state.registry
    reg.reload()
    if not _introspection_allowed(request):
        # Remote callers get liveness only — no roster size, no upstream URL.
        return JSONResponse({"status": "ok", "service": "beast-gate"})
    return JSONResponse({
        "status": "ok",
        "service": "beast-gate",
        "auth": _auth_mode(reg),
        "devices": len(reg._by_hash),
        "upstream": UPSTREAM,
    })


@asynccontextmanager
async def _lifespan(app):
    # No overall timeout (a long generation is legitimate), but connect/read
    # deadlines are essential: with timeout=None a WEDGED (not crashed)
    # llama-server pins the device's in-flight slot forever, and the caller
    # hangs with no error. Read timeout is generous and tunable.
    app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(
        None,
        connect=float(os.environ.get("OPENBEAST_EDGE_CONNECT_TIMEOUT", "10")),
        read=float(os.environ.get("OPENBEAST_EDGE_READ_TIMEOUT", "600")),
    ))
    app.state.registry = Registry()
    app.state.limiter = Limiter()
    # Mint the local token EAGERLY. _is_local() short-circuits when the header
    # is absent, so a lazy mint would leave .run/edge-local.token missing
    # until some caller happened to send one — and start.sh/doctor read that
    # file to build their own probe. It must exist the moment we serve.
    _local_token()
    try:
        yield
    finally:
        await app.state.client.aclose()


app = Starlette(
    routes=[
        Route("/gate/health", health, methods=["GET"]),
        Route("/gate/metrics", metrics, methods=["GET"]),
        Route("/{path:path}", gate,
              methods=["GET", "POST", "PUT", "DELETE", "PATCH"]),
    ],
    lifespan=_lifespan,
)


def main() -> None:
    """Bind the port FIRST, then mint the local token (D18, as artifact_server).

    uvicorn.run() runs lifespan startup — which minted the token — BEFORE it
    binds. So a second start (a double start.sh, a healthcheck --restart
    race, an operator retry) rewrote .run/edge-local.token and only then
    died on EADDRINUSE, leaving the LIVE gate holding a secret no file
    matches: doctor and start.sh silently fell back to the anonymous view.
    A start that cannot own the port now never touches the token file.
    """
    import uvicorn
    infos = socket.getaddrinfo(BIND, PORT, type=socket.SOCK_STREAM)
    infos.sort(key=lambda i: i[0] != socket.AF_INET)   # a NAME: prefer IPv4
    family, stype, proto, _, addr = infos[0]
    sock = socket.socket(family, stype, proto)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(addr)
        sock.listen(128)
    except OSError as e:
        sock.close()
        print(f"ERROR: cannot bind {BIND}:{PORT} ({e}) — another beast-gate "
              "is probably already running. Its locality token has been left "
              "alone, so doctor/start.sh introspection keeps working.",
              file=sys.stderr)
        raise SystemExit(1)
    _local_token()          # lifespan's own call is then a no-op
    reg = Registry()
    mode = {"devices": "devices",
            "anon": "ANON (OPENBEAST_EDGE_ALLOW_ANON=true)",
            "closed": "CLOSED — enroll a device: ./scripts/clients.sh enroll <id>",
            }[_auth_mode(reg)]
    print(f"beast-gate on http://{BIND}:{PORT} -> {UPSTREAM}  auth={mode}",
          flush=True)
    # Loopback by default like every other service; publish via tailscale.
    config = uvicorn.Config(app, host=BIND, port=PORT, log_level="warning")
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
