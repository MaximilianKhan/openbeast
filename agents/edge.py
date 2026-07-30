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
  OPENBEAST_EDGE_MAX_INFLIGHT  concurrent generations per device (default 2)
  OPENBEAST_EDGE_ALLOW_ANON    "true" = serve callers with no/unknown key as
                               the "anon" device (default false = fail closed)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

REPO_DIR = os.environ.get("OPENBEAST_REPO_DIR") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
RUN_DIR = os.path.join(REPO_DIR, ".run")
REGISTRY_PATH = os.path.join(RUN_DIR, "clients.json")
AUDIT_PATH = os.path.join(RUN_DIR, "inference-audit.jsonl")
# Gate-owned last-seen sidecar. Kept OUT of clients.json so the data path
# never rewrites the authorization source (see Registry.touch).
_LASTSEEN_PATH = os.path.join(RUN_DIR, "clients-lastseen.json")
_TOUCH_INTERVAL_S = 60.0
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

# Hop-by-hop headers must not be relayed (same list as agents/router.py).
# Authorization is deliberately ABSENT from the strip list on the RESPONSE
# path but is replaced on the REQUEST path: the device's key never reaches
# llama-server; we substitute the upstream key (or nothing).
_HOP_BY_HOP = {"host", "content-length", "transfer-encoding", "connection"}

# Identity headers a REMOTE client must never be able to assert for itself.
# The agent router gates its spawn path on X-OpenWebUI-User-Role, so letting
# a device set it would be privilege escalation by header.
_CLIENT_SPOOFABLE = {
    "x-openwebui-user-role", "x-openwebui-user-id", "x-openwebui-user-name",
    "x-openwebui-user-email", "x-openbeast-device",
}


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
        self._touched: dict[str, float] = {}
        self.reload()

    def reload(self) -> None:
        try:
            st = os.stat(self.path)
        except OSError:
            self._by_hash, self._present, self._mtime = {}, False, 0.0
            self._size = -1
            return
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
        except (OSError, ValueError):
            # A half-written or corrupt registry must NOT silently open the
            # door: keep serving the last good map.
            return
        by_hash = {}
        for dev in data.get("devices", []):
            key_hash = (dev.get("key_sha256") or "").strip().lower()
            if key_hash:
                by_hash[key_hash] = dev
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

    def release(self) -> None:
        # Guard against a double-release wedging the counter negative.
        if self.inflight > 0:
            self.inflight -= 1


class Limiter:
    def __init__(self):
        self._buckets: dict[str, Bucket] = {}

    def bucket(self, device: dict) -> Bucket:
        did = device.get("id", "anon")
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


def _audit(device: str, user: str | None, path: str, status: int,
           usage: dict | None, ms: int, model: str | None,
           outcome: str) -> None:
    row = {
        "ts": _now(),
        "device": device,
        "user": user,
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


def _is_loopback(request: Request) -> bool:
    """Local callers (start.sh readiness, healthcheck, doctor) skip auth on
    the /gate/* introspection routes; tailnet callers do not."""
    return _peer(request) in ("127.0.0.1", "::1", "localhost")


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
    # No registry yet. Fail CLOSED unless explicitly opted out — an empty
    # registry must not mean "everyone is welcome" (the RBAC fail-closed
    # lesson from 2026-07-17).
    if ALLOW_ANON:
        return {"id": "anon", "label": "unregistered"}, "ok"
    return None, "no_registry"


def _sanitize_body(raw: bytes, device: dict) -> tuple[bytes, str | None, bool]:
    """Strip client-controlled tenancy knobs; inject server-side affinity.

    Returns (body, model_name, streaming). Non-JSON bodies pass through.
    """
    try:
        body = json.loads(raw)
    except Exception:
        return raw, None, False
    if not isinstance(body, dict):
        return raw, None, False
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
    return json.dumps(body).encode(), body.get("model"), streaming


def _upstream_headers(request: Request, device: dict) -> dict:
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
        _log(f"denied {reason} path={path} key_fp={_key_fp(_bearer(request))} "
             f"peer={_peer(request)}")
        hint = ("this rig has no enrolled devices yet — run "
                "./scripts/clients.sh enroll <id> on the rig"
                if reason == "no_registry" else
                "present a device key: Authorization: Bearer <key>")
        return JSONResponse(
            {"error": {"message": f"unauthorized ({reason}): {hint}",
                       "type": "invalid_request_error"}}, status_code=401)

    device_id = device.get("id", "anon")
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
               int((time.monotonic() - started) * 1000), None, refusal)
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
    released = {"done": False}

    def _release():
        if not released["done"]:
            released["done"] = True
            bucket.release()

    try:
        if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
            _release()
            return JSONResponse(
                {"error": {"message": "request body too large",
                           "type": "invalid_request_error"}}, status_code=413)
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            _release()
            return JSONResponse(
                {"error": {"message": "request body too large",
                           "type": "invalid_request_error"}}, status_code=413)
        body, model, streaming = _sanitize_body(raw, device) if raw else (raw, None, False)
        headers = _upstream_headers(request, device)
        registry.touch(device_id)

        req = client.build_request(request.method, f"{UPSTREAM}{path}",
                                   content=body, headers=headers)
        try:
            resp = await client.send(req, stream=True)
        except httpx.HTTPError as e:
            _release()
            _audit(device_id, user, path, 502, None,
                   int((time.monotonic() - started) * 1000), model, "upstream_error")
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
    state = {"tail": b"", "whole": b"", "oversize": False}

    async def body_iter():
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
            _audit(device_id, user, path, resp.status_code, usage,
                   int((time.monotonic() - started) * 1000), model,
                   "ok" if resp.status_code < 400 else "upstream_status")

    return StreamingResponse(body_iter(), status_code=resp.status_code,
                             headers=hdrs)


def _introspection_allowed(request: Request) -> bool:
    """Gate the /gate/* routes.

    These expose the device roster size and per-device usage counters. The
    gate is published at the tailnet ROOT (`tailscale serve :8443 -> :8090`
    mounts `/`), so leaving them open would hand every tailnet peer — including
    one whose key was just revoked — the device list and usage telemetry.
    Loopback (start.sh readiness, healthcheck, doctor) is exempt so local
    tooling keeps working without a key.
    """
    if _is_loopback(request):
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
        "auth": "devices" if reg.configured else (
            "anon" if ALLOW_ANON else "closed"),
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


if __name__ == "__main__":
    import uvicorn
    reg = Registry()
    mode = ("devices" if reg.configured
            else ("ANON (OPENBEAST_EDGE_ALLOW_ANON=true)" if ALLOW_ANON
                  else "CLOSED — enroll a device: ./scripts/clients.sh enroll <id>"))
    print(f"beast-gate on http://{BIND}:{PORT} -> {UPSTREAM}  auth={mode}",
          flush=True)
    # Loopback by default like every other service; publish via tailscale.
    uvicorn.run(app, host=BIND, port=PORT, log_level="warning")
