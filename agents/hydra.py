#!/usr/bin/env python3
"""beast-hydra: one OpenAI-compatible front door for every inference engine.

    python3 agents/hydra.py                     # serve on 127.0.0.1:$HYDRA_PORT (8095)
    python3 agents/hydra.py --check [PATH]      # validate hydra.toml (or the implicit config)
    python3 agents/hydra.py --explain '<json>'  # dry-run a routing decision, all nodes assumed READY
    python3 agents/hydra.py --print-default-config > hydra.toml

Routes on the request's `model` (docs/BEAST_HYDRA_PLAN.md). The decision is
agents/hydra_core.py's pure `decide()`; this module is the I/O around it:
probes, the proxy with commit-point failover, the admin surface, the audit.

Invariants this file exists to keep (each has a test in tests/test_hydra_proxy.py):
  * Binds 127.0.0.1 only. Inbound calls present LLAMA_API_KEY (constant time,
    bytes); /hydra/* admin routes present the per-start local token — loopback
    alone proves nothing behind `tailscale serve`.
  * The body reaching an engine differs from the caller's ONLY in `model` and
    an `id_slot` the target cannot honour. Sampling, reasoning and message
    content are never touched.
  * Failover only BEFORE the commit point (first upstream body byte). After it,
    a failure is an SSE error event with no [DONE] — never a silent replay.
  * An engine's 4xx passes through byte for byte (runner.py compacts on the
    overflow text). A node's 401/403 is never shown as the caller's own.
  * Strict ids (/pin/<d>/…, X-Hydra-Pin, a deployment id) never substitute.
  * Every response carries X-Hydra-* provenance, and only hydra writes it (a
    node's own X-Hydra-* headers are dropped); every request one audit line,
    with no prompt or completion text in it.
  * Every pre-commit read is bounded by the attempt's deadline (an error
    body too), every failover attempt is re-vetted at admission, and a caller
    that leaves before the commit point frees its slot at once.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import hmac
import json
import os
import re
import secrets
import signal
import socket
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import hydra_core as core  # noqa: E402
from hostpolicy import trusted_hosts  # noqa: E402

REPO = HERE.parent
MAX_BODY_BYTES = 32 * 1024 * 1024          # the gate's cap
NONSTREAM_BUFFER = 16 * 1024 * 1024
ERROR_BODY_CAP = 1024 * 1024
ROUTED_PATHS = ("/v1/chat/completions", "/v1/completions", "/v1/embeddings")
_HOP = {"host", "content-length", "transfer-encoding", "connection", "keep-alive", "te", "trailer",
        "upgrade", "proxy-authorization", "proxy-authenticate", "proxy-connection"}
_TRUST_ONLY = ("x-openbeast-device",)
_REQ_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ERR_STATUS = {"hydra_unavailable": 503, "hydra_pinned_unavailable": 503, "hydra_pin_incompatible": 422,
              "hydra_unknown_model": 404, "hydra_unknown_deployment": 404, "hydra_timeout": 504,
              "hydra_upstream_auth": 502, "hydra_upstream_error": 502, "hydra_unauthorized": 401,
              "hydra_bad_request": 400}
OUTCOME_OF = {"hydra_unavailable": "unavailable", "hydra_pinned_unavailable": "pinned_unavailable",
              "hydra_pin_incompatible": "pin_incompatible", "hydra_unknown_model": "unknown_model",
              "hydra_unknown_deployment": "unknown_deployment", "hydra_timeout": "timeout",
              "hydra_upstream_auth": "upstream_auth", "hydra_upstream_error": "upstream_error",
              "hydra_bad_request": "bad_request"}
TTFT_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str) -> None:
    print(f"[hydra {datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def run_dir(env) -> Path:
    v = env.get("OPENBEAST_HYDRA_RUN_DIR") or env.get("OPENBEAST_RUN_DIR")
    return Path(v) if v else REPO / ".run"


def config_path(env) -> Path:
    p = Path(env.get("OPENBEAST_HYDRA_CONFIG") or env.get("HYDRA_CONFIG") or REPO / "hydra.toml")
    return p if p.is_absolute() else REPO / p


def _resolve(p: str, base: Path) -> Path:
    q = Path(os.path.expanduser(p))
    return q if q.is_absolute() else base / q


def _write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)        # O_TRUNC keeps an OLD mode; force it
    with os.fdopen(fd, "w") as f:
        f.write(value)


def _ct_eq(a: str, b: str) -> bool:
    # bytes, not str: compare_digest raises on non-ASCII str, and Starlette
    # decodes headers as latin-1 — a hostile byte must simply not match.
    return hmac.compare_digest(a.encode("utf-8", "surrogateescape"), b.encode("utf-8", "surrogateescape"))


def _bearer(request: Request) -> str:
    a = request.headers.get("authorization", "")
    return a[7:].strip() if a.lower().startswith("bearer ") else ""


class _FileSecret:
    """A secret read from a file, re-read when its mtime changes. Missing = None."""

    def __init__(self, path: Path):
        self.path, self._mtime, self._val = path, None, None

    def get(self) -> str | None:
        try:
            m = self.path.stat().st_mtime_ns
        except OSError:
            self._mtime, self._val = None, None
            return None
        if m != self._mtime:
            try:
                self._val = self.path.read_text().strip() or None
            except OSError:
                self._val = None
            self._mtime = m
        return self._val


def usage_from_sse(tail: bytes) -> dict | None:
    """The gate's approach: the last `data: {...usage...}` line in the tail."""
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
    except Exception:  # noqa: BLE001 — audit only
        pass
    return None


# ───────────────────────────────── audit + metrics ─────────────────────────────────

class Audit:
    """Append-only JSONL, mode 0600, rotated to .1. Never prompt or completion text."""

    def __init__(self, path: Path, max_mb: int):
        self.path, self.max_bytes = path, max_mb * 1024 * 1024

    def write(self, row: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self.path.stat().st_size >= self.max_bytes:
                    os.replace(self.path, self.path.with_name(self.path.name + ".1"))
            except FileNotFoundError:
                pass
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "a") as f:
                f.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
        except OSError as e:
            _log(f"audit write failed: {e}")


def _esc(v) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


class Metrics:
    def __init__(self):
        self.requests = collections.Counter()      # (route, deployment, outcome)
        self.attempts = collections.Counter()      # (deployment, result)
        self.failover = collections.Counter()      # (from, to, reason)
        self.ttft = collections.defaultdict(lambda: [0] * (len(TTFT_BUCKETS) + 1))
        self.ttft_sum = collections.Counter()
        self.probe_s: dict[str, float] = {}

    def observe_ttft(self, d: str, seconds: float) -> None:
        b = self.ttft[d]
        for i, le in enumerate(TTFT_BUCKETS):
            if seconds <= le:
                b[i] += 1
        b[-1] += 1
        self.ttft_sum[d] += seconds

    def render(self, hy: "Hydra") -> str:
        out = ["# TYPE hydra_requests_total counter"]
        for (r, d, o), n in sorted(self.requests.items()):
            out.append(f'hydra_requests_total{{route="{_esc(r)}",deployment="{_esc(d)}",outcome="{_esc(o)}"}} {n}')
        out.append("# TYPE hydra_inflight gauge")
        for d in hy.cfg.deployments:
            out.append(f'hydra_inflight{{deployment="{_esc(d)}"}} {hy.state.inflight(d)}')
        out.append("# TYPE hydra_attempts_total counter")
        for (d, res), n in sorted(self.attempts.items()):
            out.append(f'hydra_attempts_total{{deployment="{_esc(d)}",result="{_esc(res)}"}} {n}')
        out.append("# TYPE hydra_failover_total counter")
        for (a, b, why), n in sorted(self.failover.items()):
            out.append(f'hydra_failover_total{{from="{_esc(a)}",to="{_esc(b)}",reason="{_esc(why)}"}} {n}')
        out.append("# TYPE hydra_ttft_seconds histogram")
        for d, b in sorted(self.ttft.items()):
            for i, le in enumerate(TTFT_BUCKETS):
                out.append(f'hydra_ttft_seconds_bucket{{deployment="{_esc(d)}",le="{le}"}} {b[i]}')
            out.append(f'hydra_ttft_seconds_bucket{{deployment="{_esc(d)}",le="+Inf"}} {b[-1]}')
            out.append(f'hydra_ttft_seconds_count{{deployment="{_esc(d)}"}} {b[-1]}')
            out.append(f'hydra_ttft_seconds_sum{{deployment="{_esc(d)}"}} {self.ttft_sum[d]:.6f}')
        out.append("# TYPE hydra_deployment_state gauge")
        now = time.monotonic()
        for d, hs in sorted(hy.state.health.items()):
            for st in core.STATES:
                out.append(f'hydra_deployment_state{{deployment="{_esc(d)}",state="{st}"}} '
                           f'{1 if hs.h.state == st else 0}')
        out.append("# TYPE hydra_breaker_open gauge")
        for d, hs in sorted(hy.state.health.items()):
            out.append(f'hydra_breaker_open{{deployment="{_esc(d)}"}} '
                       f'{1 if hs.breaker_state(now) == core.OPEN else 0}')
        out.append("# TYPE hydra_probe_seconds gauge")
        for n, s in sorted(self.probe_s.items()):
            out.append(f'hydra_probe_seconds{{node="{_esc(n)}"}} {s:.6f}')
        out.append("# TYPE hydra_config_info gauge")
        out.append(f'hydra_config_info{{hash="{hy.cfg.hash}"}} 1')
        return "\n".join(out) + "\n"


# ───────────────────────────────── instinct client ─────────────────────────────────

class InstinctClient:
    """instinct-route/1 client (docs/BEAST_INSTINCT_PLAN.md §5.11).

    Fail-open by construction: any timeout, non-2xx, malformed answer, missing
    key or missing contract yields None, and hydra's static policy applies.
    A verdict is only ever APPLIED by the caller when action == "act" AND
    enforce is true; anything else is logged as shadow.
    """

    def __init__(self, cfg: core.InstinctCfg, base: Path):
        self.cfg = cfg
        self.key = _FileSecret(_resolve(cfg.key_file, base))
        self.client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(2.0, connect=0.5))
        self.contract_ok: bool | None = None
        self.checked_at = -1e9
        self.fails = 0
        self.open_until = 0.0
        self.pending = 0

    def reconfigure(self, cfg: core.InstinctCfg, base: Path) -> None:
        if cfg != self.cfg:
            self.cfg = cfg
            self.key = _FileSecret(_resolve(cfg.key_file, base))
            self.contract_ok, self.checked_at = None, -1e9

    def _headers(self) -> dict | None:
        k = self.key.get()
        return {"Authorization": f"Bearer {k}"} if k else None

    def _fail(self, now: float) -> None:
        self.fails += 1
        if self.fails >= 5:
            self.open_until, self.fails = now + 30.0, 0

    async def _contract(self, now: float) -> bool:
        if now - self.checked_at < self.cfg.contract_ttl_s and self.contract_ok is not None:
            return self.contract_ok
        self.checked_at = now
        h = self._headers()
        if h is None:
            self.contract_ok = False
            return False
        try:
            r = await self.client.get(f"{self.cfg.url}/v1/instinct/contract", headers=h, timeout=0.5)
            doc = r.json() if r.status_code == 200 else {}
            self.contract_ok = "instinct-route/1" in (doc.get("contracts") or [])
        except (httpx.HTTPError, ValueError, AttributeError):
            self.contract_ok = False
            self._fail(now)
        return self.contract_ok

    async def route(self, f: core.Features, request_id: str, client_class: str) -> dict | None:
        now = time.monotonic()
        if not self.cfg.enabled or now < self.open_until:
            return None
        if not await self._contract(now):
            return None
        h = self._headers()
        if h is None:
            return None
        body = {"contract": "instinct-route/1", "request_id": request_id, "deadline_ms": self.cfg.deadline_ms,
                "features": {"prompt_head": f.prompt_head, "est_prompt_tokens": f.est_prompt_tokens,
                             "has_images": f.has_images, "has_tools": f.has_tools, "stream": f.stream,
                             "client_class": client_class},
                "pools": None}
        t0 = time.monotonic()
        try:
            r = await asyncio.wait_for(
                self.client.post(f"{self.cfg.url}/v1/instinct/route", json=body, headers=h),
                timeout=self.cfg.deadline_ms / 1000 + 0.010)
            if r.status_code != 200:
                raise ValueError(f"status {r.status_code}")
            doc = r.json()
            if not isinstance(doc, dict) or doc.get("contract") != "instinct-route/1":
                raise ValueError("not an instinct-route/1 answer")
        except (asyncio.TimeoutError, httpx.HTTPError, ValueError) as e:
            self._fail(time.monotonic())
            return {"error": type(e).__name__, "ms": round((time.monotonic() - t0) * 1000, 1),
                    "applied": False}
        self.fails = 0
        tc = doc.get("task_class") if isinstance(doc.get("task_class"), dict) else {}
        label = tc.get("label") if isinstance(tc.get("label"), str) else None
        action = doc.get("action")
        enforce = doc.get("enforce") is True
        return {"trace_id": str(doc.get("trace_id") or "")[:80] or None, "action": action,
                "enforce": enforce, "mode": doc.get("mode"), "label": label,
                "reason": doc.get("reason"), "ms": round((time.monotonic() - t0) * 1000, 1),
                "applied": bool(action == "act" and enforce and label)}

    async def feedback(self, trace_id: str, outcome: dict) -> None:
        if not self.cfg.feedback or not trace_id or self.pending >= 32:
            return
        h = self._headers()
        if h is None:
            return
        self.pending += 1
        try:
            await self.client.post(f"{self.cfg.url}/v1/instinct/feedback",
                                   json={"trace_id": trace_id, "outcome": outcome}, headers=h, timeout=1.0)
        except httpx.HTTPError:
            pass
        finally:
            self.pending -= 1

    async def aclose(self) -> None:
        await self.client.aclose()


# ─────────────────────────────────── the service ───────────────────────────────────

class Hydra:
    def __init__(self, cfg: core.Config, *, env=None, run: Path | None = None,
                 cfg_path: Path | None = None, implicit: bool = False):
        self.env = os.environ if env is None else env
        self.cfg = cfg
        self.cfg_path, self.implicit = cfg_path, implicit
        self.run = run or run_dir(self.env)
        self.state = core.FleetState(cfg)
        self.loaded_at = _now_iso()
        self.last_reload_error: str | None = None
        self.started = time.monotonic()
        self.decisions: collections.deque = collections.deque(maxlen=200)
        self.metrics = Metrics()
        self.audit = Audit(_resolve(cfg.settings.audit, REPO), cfg.settings.audit_max_mb)
        self.local_token = secrets.token_hex(32)
        self.caller_token = _FileSecret(Path(self.env.get("OPENBEAST_HYDRA_CALLER_TOKEN_FILE")
                                             or self.run / "hydra-caller.token"))
        self.instinct = InstinctClient(cfg.settings.instinct, REPO)
        self.clients: dict[str, tuple[tuple, httpx.AsyncClient]] = {}
        self.retired: list[httpx.AsyncClient] = []
        self.probe_client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(3.0, connect=3.0))
        self.manual_drain: dict[str, str] = {}
        self.lease_drain: set[str] = set()
        self.node_meta: dict[str, dict] = {n: {} for n in cfg.nodes}
        self.next_probe: dict[str, float] = {}
        self.next_models: dict[str, float] = {}
        self.conf_mtime: dict[str, float | None] = {}
        self.conf_checked = -1e9
        self.tasks: list[asyncio.Task] = []
        self.background: set[asyncio.Task] = set()
        self.inbound_key = self._inbound_key()

    # ─── config ───
    def _inbound_key(self) -> str:
        return (self.env.get(self.cfg.settings.inbound_key_env) or "").strip()

    def reload(self) -> dict:
        try:
            if self.cfg_path is not None and self.cfg_path.exists():
                new = core.load_config(self.cfg_path, dict(self.env))
                self.implicit = False
            elif self.implicit:
                new = core.implicit_config(dict(self.env))
            else:
                raise core.ConfigError([f"{self.cfg_path} is gone — keeping the running config"])
        except core.ConfigError as e:
            self.last_reload_error = "; ".join(e.errors)
            _log(f"reload REFUSED, keeping config {self.cfg.hash}: {self.last_reload_error}")
            return {"ok": False, "errors": e.errors, "warnings": e.warnings, "config": self.cfg.hash}
        old = self.cfg
        self.cfg = new
        self.state.adopt(new)
        self.instinct.reconfigure(new.settings.instinct, REPO)
        self.audit = Audit(_resolve(new.settings.audit, REPO), new.settings.audit_max_mb)
        self.inbound_key = self._inbound_key()
        self.node_meta = {n: self.node_meta.get(n, {}) for n in new.nodes}
        self.manual_drain = {n: r for n, r in self.manual_drain.items() if n in new.nodes}
        self.lease_drain &= set(new.nodes)
        self._apply_drain()
        for nid in list(self.clients):
            if nid not in new.nodes or self._client_key(new.nodes[nid]) != self.clients[nid][0]:
                self.retired.append(self.clients.pop(nid)[1])
        self.next_probe.clear()
        self.next_models.clear()
        self.conf_checked = -1e9
        self.loaded_at = _now_iso()
        self.last_reload_error = None
        _log(f"config reloaded {old.hash} -> {new.hash}")
        return {"ok": True, "config": new.hash, "warnings": new.warnings}

    @staticmethod
    def _client_key(n: core.Node) -> tuple:
        return (n.url, n.connect_timeout_s, n.slots)

    def client_for(self, n: core.Node) -> httpx.AsyncClient:
        k = self._client_key(n)
        cur = self.clients.get(n.id)
        if cur and cur[0] == k:
            return cur[1]
        if cur:
            self.retired.append(cur[1])
        c = httpx.AsyncClient(
            trust_env=False,
            timeout=httpx.Timeout(None, connect=n.connect_timeout_s, pool=5.0),
            limits=httpx.Limits(max_connections=n.slots * 4, max_keepalive_connections=n.slots))
        self.clients[n.id] = (k, c)
        return c

    def node_key(self, n: core.Node) -> str | None:
        if n.engine == "tensorfold":
            return None                  # never, whatever the config says
        if n.key_env:
            return (self.env.get(n.key_env) or "").strip() or None
        if n.key_file:
            try:
                return _resolve(n.key_file, REPO).read_text().strip() or None
            except OSError:
                return None
        return None

    def _apply_drain(self) -> None:
        d = {n: "lease" for n in self.lease_drain}
        d.update(self.manual_drain)
        self.state.drained = d

    # ─── auth ───
    def inbound_ok(self, request: Request) -> bool:
        if not self.inbound_key:
            return True
        return _ct_eq(_bearer(request), self.inbound_key)

    def is_local(self, request: Request) -> bool:
        p = request.headers.get("x-openbeast-local", "")
        return bool(p) and _ct_eq(p, self.local_token)

    def caller(self, request: Request) -> core.Caller:
        tok = self.caller_token.get()
        presented = request.headers.get("x-hydra-caller", "")
        trusted = bool(tok and presented and _ct_eq(presented, tok))
        if not trusted:
            return core.Caller(False)
        return core.Caller(True, request.headers.get("x-openbeast-device") or None,
                           request.headers.get("x-openwebui-user-role") or None)

    # ─── probes ───
    async def probe_loop(self) -> None:
        while True:
            try:
                await self.probe_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — the prober must never die
                _log(f"probe tick failed: {type(e).__name__}: {e}")
            await asyncio.sleep(0.25)

    async def probe_tick(self, force: bool = False) -> None:
        now = time.monotonic()
        cfg = self.cfg
        due = [n for n in cfg.nodes.values() if n.enabled and (force or self.next_probe.get(n.id, 0) <= now)]
        if due:
            await asyncio.gather(*(self.probe_node(n) for n in due))
        if force or now - self.conf_checked >= 60:
            self.conf_checked = now
            self.load_conformance()
        lease_nodes = [n for n in cfg.nodes.values() if n.gpu_lease and n.enabled and n in due]
        if lease_nodes:
            held = await self.lease_held()
            for n in lease_nodes:
                if held:
                    if n.id not in self.lease_drain:
                        _log(f"node {n.id} drained: the GPU lease is held by another process")
                    self.lease_drain.add(n.id)
                else:
                    if n.id in self.lease_drain:
                        _log(f"node {n.id} undrained: the GPU lease is free")
                    self.lease_drain.discard(n.id)
            self._apply_drain()

    def _deps_of(self, node_id: str) -> list[core.Deployment]:
        return [d for d in self.cfg.deployments.values() if d.node == node_id]

    async def probe_node(self, n: core.Node) -> None:
        s = self.cfg.settings
        t0 = time.monotonic()
        try:
            r = await self.probe_client.get(f"{n.url}/health", timeout=3.0)
            result = core.engine_ready(n.engine, r.status_code, r.content)
            detail = f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            result, detail = "down", type(e).__name__
        now = time.monotonic()
        self.metrics.probe_s[n.id] = now - t0
        self.node_meta.setdefault(n.id, {})["last_probe"] = {
            "t": _now_iso(), "ms": round((now - t0) * 1000, 1), "result": result, "detail": detail}
        deps = self._deps_of(n.id)
        became_ready = False
        for d in deps:
            hs = self.state.health.get(d.id)
            if hs is None:
                continue
            before = hs.h.state
            after = hs.on_probe(result, now)
            if after != before:
                _log(f"{d.id}: {before} -> {after} ({detail})")
            became_ready |= after == core.READY and before != core.READY
        states = [self.state.health[d.id].h.state for d in deps if d.id in self.state.health]
        bad = bool(states) and all(x in (core.DOWN, core.AUTH_FAILED, core.MISMATCH) for x in states)
        # UNKNOWN (boot, a new node) is probed every second, so up_after is
        # reached in seconds rather than up_after x probe_interval.
        fresh = any(x == core.UNKNOWN for x in states)
        self.next_probe[n.id] = now + (s.probe_down_interval_s if bad else
                                       min(1.0, s.probe_interval_s) if fresh else s.probe_interval_s)
        stuck = any(self.state.health[d.id].h.state in (core.AUTH_FAILED, core.MISMATCH)
                    for d in deps if d.id in self.state.health)
        if result == "ready" and (became_ready or stuck or self.next_models.get(n.id, 0) <= now):
            await self.check_models(n, deps)

    async def check_models(self, n: core.Node, deps: list[core.Deployment]) -> None:
        s = self.cfg.settings
        now = time.monotonic()
        self.next_models[n.id] = now + (s.probe_down_interval_s if any(
            self.state.health[d.id].h.state in (core.AUTH_FAILED, core.MISMATCH) for d in deps
            if d.id in self.state.health) else s.models_interval_s)
        hdr = {}
        key = self.node_key(n)
        if key:
            hdr["Authorization"] = f"Bearer {key}"
        try:
            r = await self.probe_client.get(f"{n.url}/v1/models", headers=hdr, timeout=5.0)
        except httpx.HTTPError:
            return
        now = time.monotonic()
        if r.status_code in (401, 403):
            for d in deps:
                self._transition(d, "auth", now, f"/v1/models {r.status_code} with the node key")
            return
        if r.status_code != 200:
            return
        try:
            ids = {m.get("id") for m in r.json().get("data", []) if isinstance(m, dict)}
        except (ValueError, AttributeError):
            return
        for d in deps:
            if d.verify_upstream and d.upstream not in ids:
                served = ", ".join(repr(x) for x in sorted(i for i in ids if isinstance(i, str))[:4])
                self._transition(d, "mismatch", now,
                                 f"{d.upstream!r} not in /v1/models (it lists {served or 'nothing'})")
            else:
                self._transition(d, "ok", now)

    def _transition(self, d: core.Deployment, result: str, now: float, detail: str = "") -> None:
        hs = self.state.health.get(d.id)
        if hs is None:
            return
        before = hs.h.state
        after = hs.on_models(result, now, detail)
        if before != after:
            _log(f"{d.id}: {before} -> {after} {detail}")

    async def lease_held(self) -> bool:
        cmd = self.env.get("OPENBEAST_HYDRA_LEASE_CMD") or str(REPO / "scripts" / "gpu-lease.sh")
        try:
            p = await asyncio.create_subprocess_exec(
                cmd, "check", stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
        except OSError as e:
            _log(f"WARNING: GPU lease check could not run ({e}) — not draining")
            return False
        try:
            rc = await asyncio.wait_for(p.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                p.kill()
            await p.wait()
            _log("WARNING: GPU lease check timed out — not draining")
            return False
        if rc not in (0, 3, 4):
            _log(f"WARNING: GPU lease check exited {rc} — not draining")
        return rc == 4

    def load_conformance(self) -> None:
        for d in self.cfg.deployments.values():
            if d.conformance == "off":
                self.state.conformance.pop(d.id, None)
                continue
            p = self.run / "conformance" / d.id / "latest.json"
            try:
                m = p.stat().st_mtime
            except OSError:
                self.state.conformance[d.id] = ("missing", False)
                self.conf_mtime[d.id] = None
                continue
            if self.conf_mtime.get(d.id) == m and d.id in self.state.conformance:
                continue
            self.conf_mtime[d.id] = m
            try:
                doc = json.loads(p.read_text())
            except (OSError, ValueError):
                doc = None
            self.state.conformance[d.id] = core.conformance_verdict(doc, self.cfg.nodes[d.node].url)

    # ─── status ───
    def routable(self, route_id: str) -> tuple[bool, dict]:
        cfg = self.cfg
        r = cfg.routes.get(route_id)
        if r is None:
            return False, {}
        now = time.monotonic()
        groups: dict[int, int] = {}
        for tg in r.targets:
            d = cfg.deployments[tg.d]
            n = cfg.nodes[d.node]
            why = core._exclude(d, n, self.state, core.Features(), set(r.require), r, {}, now, cfg,
                                check_ctx=False)
            if why is None:
                groups[tg.priority] = groups.get(tg.priority, 0) + 1
        return bool(groups), {str(k): v for k, v in sorted(groups.items())}

    def deployment_routable(self, d: core.Deployment) -> bool:
        n = self.cfg.nodes[d.node]
        return core._exclude(d, n, self.state, core.Features(), set(), None, {}, time.monotonic(),
                             self.cfg, check_ctx=False) is None

    def status(self) -> dict:
        cfg = self.cfg
        now = time.monotonic()
        nodes = {}
        for n in cfg.nodes.values():
            nodes[n.id] = {"host": core.host_class(n.url)[0], "engine": n.engine, "enabled": n.enabled,
                           "drained": self.state.drained.get(n.id), "key": "set" if self.node_key(n) else "none",
                           "slots": n.slots, "inflight": self.state.node_inflight(n.id),
                           "last_probe": self.node_meta.get(n.id, {}).get("last_probe")}
        deps = {}
        for d in cfg.deployments.values():
            hs = self.state.health[d.id]
            deps[d.id] = {"node": d.node, "state": hs.h.state, "detail": hs.h.detail,
                          "breaker": hs.breaker_state(now), "inflight": self.state.inflight(d.id),
                          "slots": cfg.nodes[d.node].slots, "caps": sorted(self.state.effective_caps(d)),
                          "ctx": d.ctx, "upstream": d.upstream, "family": d.family,
                          "conformance": (self.state.conformance.get(d.id, ("missing", False))[0]
                                          if d.conformance != "off" else "n/a"),
                          "routable": self.deployment_routable(d),
                          "served_total": hs.h.served_total, "fail_total": hs.h.fail_total,
                          "ttft_ewma_ms": None if hs.h.ttft_ewma_ms is None else round(hs.h.ttft_ewma_ms, 1)}
        routes = {}
        for r in cfg.routes.values():
            ok, groups = self.routable(r.id)
            routes[r.id] = {"routable": ok, "candidates_per_group": groups, "aliases": list(r.aliases)}
        return {"config_hash": cfg.hash, "config_source": cfg.source, "implicit": self.implicit,
                "loaded_at": self.loaded_at, "last_reload_error": self.last_reload_error,
                "uptime_s": round(time.monotonic() - self.started, 1), "default_route": cfg.default_route,
                "warnings": cfg.warnings, "audit": str(self.audit.path), "nodes": nodes,
                "deployments": deps, "routes": routes, "decisions": list(self.decisions)[-50:]}

    # ─── responses ───
    def hydra_headers(self, request_id: str, *, route: str | None = None, rules=(), cand=None,
                      attempts: list | None = None) -> dict:
        h = {"X-Hydra-Request-Id": request_id, "X-Hydra-Config": self.cfg.hash}
        if route:
            h["X-Hydra-Route"] = route
        if rules:
            h["X-Hydra-Rule"] = ",".join(rules)
        if cand is not None:
            h.update({"X-Hydra-Deployment": cand.d.id, "X-Hydra-Node": cand.n.id,
                      "X-Hydra-Engine": cand.n.engine, "X-Hydra-Upstream-Model": cand.d.upstream})
        if attempts:
            h["X-Hydra-Attempts"] = ",".join(f"{a['d']}:{a['status']}" for a in attempts)
        return h

    def error(self, etype: str, message: str, headers: dict, extra: dict | None = None,
              retry_after: int | None = None, status: int | None = None) -> JSONResponse:
        body = {"error": {"message": message, "type": etype}}
        if extra is not None:
            body["error"]["hydra"] = extra
        h = dict(headers)
        if retry_after:
            h["Retry-After"] = str(retry_after)
        return JSONResponse(body, status_code=status or ERR_STATUS.get(etype, 502), headers=h)


# ─────────────────────────────────── proxy ───────────────────────────────────

class _Attempt:
    """What one pre-commit attempt produced."""

    def __init__(self):
        self.kind = ""            # commit | fail | passthrough | timeout | auth | mismatch | throttled | loading
        self.status: int | str = ""
        self.resp: httpx.Response | None = None
        self.first: bytes = b""
        self.it = None
        self.body: bytes | None = None
        self.headers: dict = {}
        self.ttft_s: float | None = None
        self.complete = False


def _upstream_headers(request: Request, caller: core.Caller, key: str | None, request_id: str) -> dict:
    h = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in _HOP or lk == "authorization" or lk.startswith("x-hydra-") or lk == "x-openbeast-local":
            continue
        if lk in ("accept-encoding", "x-openbeast-request-id"):
            continue
        if not caller.trusted and (lk in _TRUST_ONLY or lk.startswith("x-openwebui-user-")):
            continue
        h[k] = v
    h["content-type"] = "application/json"
    h["accept-encoding"] = "identity"
    h["X-OpenBeast-Request-Id"] = request_id
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _resp_headers(resp: httpx.Response) -> dict:
    # X-Hydra-* is hydra's provenance and only hydra writes it: a node that
    # sends its own X-Hydra-Deployment would otherwise ship next to ours
    # (different case, both kept) and a client's .get() would read the forgery.
    return {k: v for k, v in resp.headers.items()
            if k.lower() not in _HOP and not k.lower().startswith("x-hydra-")}


class _UpstreamTruncated(Exception):
    """Raised out of a non-SSE relay so the server aborts the response
    instead of ending it cleanly: a truncated JSON body must look broken."""


async def _attempt(hy: Hydra, c: core.Candidate, path: str, payload: bytes, headers: dict,
                   stream: bool, deadline: float) -> _Attempt:
    """One upstream try, up to (not past) the commit point."""
    a = _Attempt()
    client = hy.client_for(c.n)
    t0 = time.monotonic()
    req = client.build_request("POST", f"{c.n.url}{path}", content=payload, headers=headers)
    resp = None

    def left() -> float:
        return max(0.001, deadline - time.monotonic())

    async def read_error_body() -> bytes:
        chunks, size = [], 0
        async for ch in resp.aiter_raw():
            size += len(ch)
            if size <= ERROR_BODY_CAP:
                chunks.append(ch)
        return b"".join(chunks)

    try:
        resp = await asyncio.wait_for(client.send(req, stream=True), timeout=max(0.001, deadline - t0))
        a.resp = resp
        a.status = resp.status_code
        if resp.status_code >= 300:
            # The whole error body under the SAME deadline: a node that sends
            # a status line and then stalls must not hold the attempt (and
            # its slot) past the TTFT deadline and eat the failover budget.
            try:
                a.body = await asyncio.wait_for(read_error_body(), timeout=left())
            except (asyncio.TimeoutError, httpx.HTTPError) as e:
                # The status already said "error"; without its body it can
                # not be passed through, so it is a plain failure: fail over.
                a.kind = "fail"
                a.headers = {"_why": f"{resp.status_code} with an unreadable body ({type(e).__name__})"}
                with contextlib.suppress(BaseException):
                    await resp.aclose()
                return a
            a.headers = _resp_headers(resp)
            await resp.aclose()
            text = a.body.decode("utf-8", "replace")
            st = resp.status_code
            if st in (401, 403):
                a.kind = "auth"
            elif core.is_model_404(st, text) and c.n.engine != "llama":
                a.kind = "mismatch"
            elif st == 429:
                a.kind = "throttled"
            elif st == 503 and "Loading model" in text:
                a.kind = "loading"
            elif st >= 500:
                a.kind = "fail"
            else:
                a.kind = "passthrough"
            return a
        a.headers = _resp_headers(resp)
        it = resp.aiter_raw()
        if stream:
            try:
                a.first = await asyncio.wait_for(it.__anext__(), timeout=left())
            except StopAsyncIteration:
                a.first = b""
            if not a.first:
                # A clean zero-byte 2xx is not an answer: nothing was
                # committed, so fail over rather than hand the caller "".
                return await _empty(a, resp)
            a.ttft_s = time.monotonic() - t0
            a.it, a.kind = it, "commit"
            return a
        buf = bytearray()
        while True:
            try:
                ch = await asyncio.wait_for(it.__anext__(), timeout=left())
            except StopAsyncIteration:
                a.complete = True
                break
            if a.ttft_s is None:
                a.ttft_s = time.monotonic() - t0
            buf += ch
            if len(buf) > NONSTREAM_BUFFER:
                a.it = it                   # too big to hold: commit and relay the rest
                break
        if not buf:
            return await _empty(a, resp)
        a.first, a.kind = bytes(buf), "commit"
        if a.ttft_s is None:
            a.ttft_s = time.monotonic() - t0
        return a
    except asyncio.TimeoutError:
        a.kind, a.status = "timeout", "timeout"
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        a.kind, a.status = "fail", "connect"
        a.headers = {"_why": type(e).__name__}
    except httpx.HTTPError as e:
        a.kind, a.status = "fail", "transport"
        a.headers = {"_why": type(e).__name__}
    except BaseException:
        # cancelled (the caller left): never strand the upstream connection
        if resp is not None:
            with contextlib.suppress(BaseException):
                await resp.aclose()
        raise
    if resp is not None:
        with contextlib.suppress(BaseException):
            await resp.aclose()
    return a


async def _empty(a: _Attempt, resp: httpx.Response) -> _Attempt:
    a.kind, a.status, a.it = "fail", "empty", None
    a.headers = {"_why": f"{resp.status_code} with an empty body"}
    with contextlib.suppress(BaseException):
        await resp.aclose()
    return a


async def _until_disconnect(request: Request, coro):
    """Run `coro`; if the caller hangs up first, cancel it and return None.

    Before the commit point nothing reads the client socket, so without this
    a caller that gave up keeps a slot (and the engine) busy until the
    upstream answers — and a non-stream request is then audited as "ok"."""
    task = asyncio.ensure_future(coro)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=0.25)
            if done:
                return task.result()
            if await request.is_disconnected():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                return None
    except BaseException:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        raise


def _client_class(caller: core.Caller, request: Request) -> str:
    if caller.trusted and (caller.device or caller.role):
        return "interactive"
    return "agent"


async def proxy(request: Request, path: str, pin: str | None = None):
    hy: Hydra = request.app.state.hydra
    cfg = hy.cfg                              # one snapshot for the whole request
    started = time.monotonic()
    rid_in = request.headers.get("x-openbeast-request-id", "")
    request_id = rid_in if _REQ_ID_RE.match(rid_in) else uuid.uuid4().hex[:16]
    base_h = hy.hydra_headers(request_id)
    if not hy.inbound_ok(request):
        return hy.error("hydra_unauthorized", "present the inference key: Authorization: Bearer <LLAMA_API_KEY>",
                        base_h)
    if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        return hy.error("hydra_bad_request", "request body too large", base_h, status=413)
    buf = bytearray()
    async for ch in request.stream():
        buf += ch
        if len(buf) > MAX_BODY_BYTES:
            return hy.error("hydra_bad_request", "request body too large", base_h, status=413)
    try:
        body = json.loads(bytes(buf) or b"null")
    except ValueError:
        body = None
    caller = hy.caller(request)
    row = {"ts": _now_iso(), "request_id": request_id, "device": caller.device, "role": caller.role,
           "trusted": caller.trusted, "path": path, "config": cfg.hash}
    if not isinstance(body, dict):
        row.update(requested=None, strict=bool(pin), status=400, outcome="bad_request",
                   ms=int((time.monotonic() - started) * 1000))
        hy.audit.write(row)
        return hy.error("hydra_bad_request", "the request body must be a JSON object", base_h)
    f = core.extract_features(path, body, request.headers, cfg)
    now = time.monotonic()
    ins = None
    task_class = None
    if not pin and cfg.settings.instinct.enabled and \
            core.instinct_worthwhile(cfg, hy.state, f, caller, now):
        ins = await hy.instinct.route(f, request_id, _client_class(caller, request))
        if ins and ins.get("applied"):
            task_class = ins["label"]
    now = time.monotonic()
    dec = core.decide(cfg, hy.state, f, caller, now, pin=pin, task_class=task_class)
    if ins and not ins.get("applied") and ins.get("action") == "act" and ins.get("label"):
        shadow = core.decide(cfg, hy.state, f, caller, now, pin=pin, task_class=ins["label"])
        ins["shadow_route"] = shadow.route
        ins["shadow_deployment"] = shadow.attempts[0].d.id if shadow.ok and shadow.attempts else None
    trace = dec.as_dict()
    trace.update(request_id=request_id, ts=row["ts"])
    hy.decisions.append(trace)
    row.update(requested=f.model, strict=dec.strict, rules=dec.trace.rules, route=dec.route,
               features=f.public(), excluded=dec.trace.excluded, instinct=ins)
    hdr = hy.hydra_headers(request_id, route=dec.route, rules=dec.trace.rules)

    def finish(status, outcome, deployment=None, attempts=None, usage=None, edits=None):
        row.update(status=status, outcome=outcome, deployment=deployment, attempts=attempts or [],
                   usage=usage, body_edits=edits or [], ms=int((time.monotonic() - started) * 1000))
        hy.audit.write(row)
        hy.metrics.requests[(dec.route or "-", deployment or "-", outcome)] += 1
        if ins and ins.get("trace_id") and cfg.settings.instinct.feedback:
            ttft = next((a.get("ttft_ms") for a in reversed(attempts or []) if a.get("ttft_ms") is not None), None)
            fb = {"source": "hydra", "served_pool": deployment, "ttft_ms": ttft, "status": status,
                  "outcome": outcome, "error": None if outcome == "ok" else outcome, "route": dec.route}
            with contextlib.suppress(RuntimeError):
                # keep a reference: the loop holds tasks weakly
                t = asyncio.get_running_loop().create_task(hy.instinct.feedback(ins["trace_id"], fb))
                hy.background.add(t)
                t.add_done_callback(hy.background.discard)

    if not dec.ok:
        finish(dec.status, OUTCOME_OF.get(dec.error_type, "error"))
        extra = {"route": dec.route, "excluded": dec.trace.excluded, "config": cfg.hash}
        return hy.error(dec.error_type, dec.message, hdr, extra, dec.retry_after, status=dec.status)

    s = cfg.settings
    budget_end = started + s.pre_commit_budget_s
    attempts: list[dict] = []
    route = cfg.routes.get(dec.route) if not dec.strict else None
    last = None
    for i, c in enumerate(dec.attempts):
        now = time.monotonic()
        if now >= budget_end:
            break
        fbody, edits = core.forward_body(body, c.d, c.n)
        payload = json.dumps(fbody, ensure_ascii=False).encode()
        uh = _upstream_headers(request, caller, hy.node_key(c.n), request_id)
        if f.stream:
            limit = core.effective_ttft(c.n, f.est_prompt_tokens, s.pre_commit_budget_s)
        else:
            limit = c.n.nonstream_timeout_s
        deadline = min(now + limit, budget_end)
        # Re-vetted at admission, not only in decide(): the plan is stale by
        # the time a failover attempt runs (a HALF_OPEN trial taken, a node
        # gone DOWN or drained). Attempt 0 follows decide() synchronously.
        adm, why = hy.state.try_admit(c.d.id, c.n.id, now)
        if adm is None:
            attempts.append({"d": c.d.id, "node": c.n.id, "engine": c.n.engine, "status": "skipped",
                             "ttft_ms": None, "outcome": "skipped", "why": why})
            continue
        hs = adm.hs                       # survives a reload that drops the deployment
        try:
            a = await _until_disconnect(request, _attempt(hy, c, path, payload, uh, f.stream, deadline))
        except BaseException:
            adm.release()
            raise
        if a is None:                     # the caller hung up before the commit point
            adm.release()
            attempts.append({"d": c.d.id, "node": c.n.id, "engine": c.n.engine, "status": "client_gone",
                             "ttft_ms": None, "outcome": "client_disconnect"})
            finish(499, "client_disconnect", c.d.id, attempts)
            return Response(b"", status_code=499, headers=hy.hydra_headers(request_id, route=dec.route))
        now = time.monotonic()
        rec = {"d": c.d.id, "node": c.n.id, "engine": c.n.engine, "status": a.status,
               "ttft_ms": None if a.ttft_s is None else int(a.ttft_s * 1000), "outcome": a.kind}
        attempts.append(rec)
        hy.metrics.attempts[(c.d.id, a.kind)] += 1
        last = (c, a, edits, adm)
        if a.kind == "commit":
            break                         # the committed attempt keeps its in-flight unit
        adm.release()
        if a.kind == "passthrough":
            break
        if a.kind == "timeout":
            hs.record_failure(now)
            if not (route and route.retry_on_ttft_timeout):
                break
        elif a.kind == "fail":
            hs.record_failure(now)
        elif a.kind == "loading":
            hs.request_state("loading", now)
        elif a.kind == "auth":
            hs.request_state("auth", now, f"HTTP {a.status} on a request")
        elif a.kind == "mismatch":
            hs.request_state("mismatch", now, f"404 model on a request ({c.d.upstream!r})")
        nxt = dec.attempts[i + 1].d.id if i + 1 < len(dec.attempts) else "-"
        hy.metrics.failover[(c.d.id, nxt, a.kind)] += 1

    hdr = hy.hydra_headers(request_id, route=dec.route, rules=dec.trace.rules,
                           cand=last[0] if last else None, attempts=attempts)
    if last is None:
        if attempts:                      # every planned target became ineligible meanwhile
            finish(503, "unavailable", attempts=attempts)
            return hy.error("hydra_unavailable", "no planned deployment could be admitted "
                            f"({hdr.get('X-Hydra-Attempts', '')})", hdr, retry_after=5)
        finish(504, "timeout", attempts=attempts)
        return hy.error("hydra_timeout", "the pre-commit budget ran out before any attempt", hdr)
    c, a, edits, adm = last
    if a.kind == "passthrough" or (a.kind == "throttled" and not _has_more(dec, c)):
        # An engine's own 4xx: status, bytes and content-type exactly as sent.
        finish(a.status, "upstream_4xx", c.d.id, attempts, edits=edits)
        h = {k: v for k, v in a.headers.items() if k.lower() in ("content-type", "retry-after")}
        h.update(hdr)
        return Response(a.body or b"", status_code=int(a.status), headers=h)
    if a.kind != "commit":
        if a.kind == "timeout":
            finish(504, "timeout", c.d.id, attempts)
            return hy.error("hydra_timeout", f"{c.d.id} produced no first byte in time", hdr)
        if a.kind == "auth" and all(x["outcome"] in ("auth", "skipped") for x in attempts):
            finish(502, "upstream_auth", c.d.id, attempts)
            return hy.error("hydra_upstream_auth", "the node refused hydra's key (AUTH_FAILED); "
                            "see scripts/hydra.sh status", hdr)
        finish(502, "upstream_error", c.d.id, attempts)
        return hy.error("hydra_upstream_error",
                        f"every attempt failed before the first byte ({hdr.get('X-Hydra-Attempts', '')})", hdr)

    # ── committed ──
    if f.session_key and dec.route and not dec.strict and route and route.affinity != "none":
        hy.state.affinity.put(f.session_key, c.d.id, time.monotonic())
    hs = adm.hs
    if a.ttft_s is not None:
        hy.metrics.observe_ttft(c.d.id, a.ttft_s)
        ms = a.ttft_s * 1000
        hs.h.ttft_ewma_ms = ms if hs.h.ttft_ewma_ms is None else 0.8 * hs.h.ttft_ewma_ms + 0.2 * ms
    out_h = {k: v for k, v in a.headers.items() if not k.startswith("_")}   # no x-hydra-*: _resp_headers
    out_h.update(hdr)
    if a.it is None:
        # non-stream (or a stream that ended at once): the whole body is in hand
        adm.release()
        hs.record_success(time.monotonic())
        usage = None
        with contextlib.suppress(ValueError, AttributeError):
            usage = json.loads(a.first or b"{}").get("usage")
        if usage is None:
            usage = usage_from_sse(a.first[-16384:])
        finish(a.status, "ok", c.d.id, attempts, usage, edits)
        out_h.pop("content-length", None)
        return Response(a.first, status_code=int(a.status), headers=out_h)

    st = {"tail": a.first[-16384:], "done": False}
    idle = c.n.idle_timeout_s
    is_sse = "text/event-stream" in (a.headers.get("content-type") or "")

    def close_out(outcome: str, status: int) -> None:
        if st["done"]:
            return
        st["done"] = True
        adm.release()
        finish(status, outcome, c.d.id, attempts, usage_from_sse(st["tail"]), edits)

    async def relay():
        completed = upstream_failed = False
        try:
            if a.first:
                yield a.first
            while True:
                try:
                    ch = await asyncio.wait_for(a.it.__anext__(), timeout=idle)
                except StopAsyncIteration:
                    break
                st["tail"] = (st["tail"] + ch)[-16384:]
                yield ch
            completed = True
        except (asyncio.TimeoutError, httpx.HTTPError):
            upstream_failed = True
            hs.record_failure(time.monotonic())
            attempts[-1]["outcome"] = "upstream_failed_midstream"
            _log(f"{c.d.id}: upstream failed mid-stream request_id={request_id}")
            # Loud truncation: an error event and NO [DONE]. Never a replay.
            if is_sse:
                yield core.sse_error_event(c.n.id, c.d.id, request_id)
        finally:
            with contextlib.suppress(BaseException):
                await a.resp.aclose()
            if completed:
                hs.record_success(time.monotonic())
                close_out("ok", int(a.status))
            elif upstream_failed:
                close_out("upstream_failed_midstream", int(a.status))
            else:
                close_out("client_disconnect", int(a.status))
        if upstream_failed and not is_sse:
            # No event format to carry the error: abort the response so the
            # client sees a broken body, never a clean, silently short one.
            raise _UpstreamTruncated(f"{c.d.id} failed mid-body (request_id={request_id})")

    async def sweep():
        # The body iterator may never start (client gone right after headers).
        with contextlib.suppress(BaseException):
            await a.resp.aclose()
        close_out("client_disconnect", int(a.status))

    out_h.pop("content-length", None)
    return StreamingResponse(relay(), status_code=int(a.status), headers=out_h,
                             background=BackgroundTask(sweep))


def _has_more(dec: core.Decision, c: core.Candidate) -> bool:
    ids = [x.d.id for x in dec.attempts]
    return c.d.id in ids and ids.index(c.d.id) < len(ids) - 1


# ─────────────────────────────────── handlers ───────────────────────────────────

def _hy(request: Request) -> Hydra:
    return request.app.state.hydra


async def health(request: Request):
    hy = _hy(request)
    ok, _ = hy.routable(hy.cfg.default_route)
    if ok:
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "loading", "hydra": f"no routable deployment for {hy.cfg.default_route}"},
                        status_code=503)


def _model_entry(hy: Hydra, kind: str, mid: str, **meta) -> dict:
    return {"id": mid, "object": "model", "owned_by": "hydra", "created": 0, "openbeast": {"kind": kind, **meta}}


def _route_entry(hy: Hydra, r: core.Route) -> dict:
    cfg = hy.cfg
    caps = set()
    max_ctx = 0
    for t in r.targets:
        d = cfg.deployments[t.d]
        caps |= hy.state.effective_caps(d)
        max_ctx = max(max_ctx, d.ctx)
    ok, _ = hy.routable(r.id)
    return _model_entry(hy, "route", r.id, strict=False, description=r.description, caps=sorted(caps),
                        max_ctx=max_ctx, healthy=ok, aliases=list(r.aliases),
                        targets=[{"d": t.d, "priority": t.priority} for t in r.targets])


def _dep_entry(hy: Hydra, d: core.Deployment) -> dict:
    return _model_entry(hy, "deployment", d.id, strict=True, description=f"{d.upstream} on {d.node}",
                        caps=sorted(hy.state.effective_caps(d)), max_ctx=d.ctx,
                        healthy=hy.deployment_routable(d), targets=[])


async def models(request: Request):
    hy = _hy(request)
    rid = uuid.uuid4().hex[:16]
    if not hy.inbound_ok(request):
        return hy.error("hydra_unauthorized", "present the inference key", hy.hydra_headers(rid))
    cfg = hy.cfg
    data = [_route_entry(hy, cfg.routes[cfg.default_route])]
    data += [_route_entry(hy, r) for r in cfg.routes.values() if r.listed and r.id != cfg.default_route]
    data += [_dep_entry(hy, d) for d in cfg.deployments.values() if d.listed]
    return JSONResponse({"object": "list", "data": data}, headers=hy.hydra_headers(rid))


async def routed(request: Request):
    return await proxy(request, request.url.path.rstrip("/"))


async def pinned(request: Request):
    hy = _hy(request)
    d = request.path_params["d"]
    rest = "/" + request.path_params["rest"].strip("/")
    rid = uuid.uuid4().hex[:16]
    h = hy.hydra_headers(rid, route="pin")
    if not hy.inbound_ok(request):
        return hy.error("hydra_unauthorized", "present the inference key", h)
    dep = hy.cfg.deployments.get(d)
    if dep is None:
        return hy.error("hydra_unknown_deployment", f"no deployment {d!r}", h)
    if request.method == "GET" and rest == "/v1/models":
        return JSONResponse({"object": "list", "data": [_dep_entry(hy, dep)]}, headers=h)
    if request.method == "POST" and rest in ROUTED_PATHS:
        return await proxy(request, rest, pin=d)
    if request.method == "GET" and rest in ("/health", "/props", "/slots"):
        n = hy.cfg.nodes[dep.node]
        if n.engine != "llama":
            return hy.error("hydra_not_routed", f"/pin/{d}{rest} is llama.cpp-only", h, status=404)
        hdr = {}
        key = hy.node_key(n)
        if key and rest != "/health":
            hdr["Authorization"] = f"Bearer {key}"
        try:
            r = await hy.client_for(n).get(f"{n.url}{rest}", headers=hdr, timeout=10.0)
        except httpx.HTTPError as e:
            return hy.error("hydra_pinned_unavailable", f"{d}: {type(e).__name__}", h, retry_after=5)
        out = {k: v for k, v in r.headers.items() if k.lower() in ("content-type",)}
        out.update(h)
        out.update({"X-Hydra-Deployment": dep.id, "X-Hydra-Node": n.id, "X-Hydra-Engine": n.engine,
                    "X-Hydra-Upstream-Model": dep.upstream})
        return Response(r.content, status_code=r.status_code, headers=out)
    return not_routed(request)


def not_routed(request: Request):
    return JSONResponse({"error": {"type": "hydra_not_routed", "message": (
        "hydra routes /v1 only; address a node directly or use /pin/<deployment>/")}}, status_code=404)


async def catch_all(request: Request):
    return not_routed(request)


def _admin(fn):
    async def wrapped(request: Request):
        hy = _hy(request)
        if not hy.is_local(request):
            # 403 even from loopback: `tailscale serve` makes every tailnet
            # peer look like 127.0.0.1 (plan F8).
            return JSONResponse({"error": {"type": "hydra_forbidden",
                                           "message": "admin routes need the X-OpenBeast-Local token"}},
                                status_code=403)
        return await fn(request, hy)
    return wrapped


@_admin
async def a_status(request: Request, hy: Hydra):
    return JSONResponse(hy.status())


@_admin
async def a_explain(request: Request, hy: Hydra):
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        return hy.error("hydra_bad_request", "explain takes a JSON request body", {})
    if not isinstance(body, dict):
        return hy.error("hydra_bad_request", "explain takes a JSON object", {})
    return JSONResponse(explain(hy.cfg, hy.state, body))


def explain(cfg: core.Config, state: core.FleetState, body: dict) -> dict:
    body = dict(body)
    headers = {str(k).lower(): str(v) for k, v in (body.pop("headers", None) or {}).items()}
    path = body.pop("path", None) or "/v1/chat/completions"
    task_class = body.pop("task_class", None)
    pin = body.pop("pin", None)
    trusted = str(body.pop("trusted", "")).lower() == "true"
    caller = core.Caller(trusted, headers.get("x-openbeast-device"), headers.get("x-openwebui-user-role"))
    f = core.extract_features(path, body, headers, cfg)
    dec = core.decide(cfg, state, f, caller, time.monotonic(), pin=pin, task_class=task_class)
    out = dec.as_dict()
    out["features"] = f.public()
    out["config"] = cfg.hash
    if dec.ok and dec.attempts:
        c = dec.attempts[0]
        out["body_edits"] = core.forward_body(body, c.d, c.n)[1]
        out["effective_ttft_s"] = core.effective_ttft(c.n, f.est_prompt_tokens, cfg.settings.pre_commit_budget_s)
    return out


@_admin
async def a_decisions(request: Request, hy: Hydra):
    try:
        n = max(1, min(200, int(request.query_params.get("n", "200"))))
    except ValueError:
        n = 200
    return JSONResponse({"decisions": list(hy.decisions)[-n:]})


@_admin
async def a_reload(request: Request, hy: Hydra):
    res = hy.reload()
    return JSONResponse(res, status_code=200 if res["ok"] else 422)


@_admin
async def a_drain(request: Request, hy: Hydra):
    node = request.path_params["node"]
    if node not in hy.cfg.nodes:
        return JSONResponse({"ok": False, "error": f"no node {node!r}"}, status_code=404)
    hy.manual_drain[node] = "manual"
    hy._apply_drain()
    _log(f"node {node} drained (manual)")
    return JSONResponse({"ok": True, "node": node, "drained": "manual"})


@_admin
async def a_undrain(request: Request, hy: Hydra):
    node = request.path_params["node"]
    if node not in hy.cfg.nodes:
        return JSONResponse({"ok": False, "error": f"no node {node!r}"}, status_code=404)
    hy.manual_drain.pop(node, None)
    hy._apply_drain()
    _log(f"node {node} undrained (manual)")
    return JSONResponse({"ok": True, "node": node, "drained": hy.state.drained.get(node)})


@_admin
async def a_metrics(request: Request, hy: Hydra):
    return Response(hy.metrics.render(hy), media_type="text/plain; version=0.0.4")


# ─────────────────────────────────── app ───────────────────────────────────

def create_app(hy: Hydra, *, probe: bool = True) -> Starlette:
    @contextlib.asynccontextmanager
    async def lifespan(app):
        app.state.hydra = hy
        _write_secret(hy.run / "hydra-local.token", hy.local_token)
        if probe:
            hy.tasks.append(asyncio.create_task(hy.probe_loop()))
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
            asyncio.get_running_loop().add_signal_handler(signal.SIGHUP, hy.reload)
        try:
            yield
        finally:
            for t in hy.tasks:
                t.cancel()
            for t in hy.tasks:
                with contextlib.suppress(BaseException):
                    await t
            for _, c in hy.clients.values():
                await c.aclose()
            for c in hy.retired:
                await c.aclose()
            await hy.probe_client.aclose()
            await hy.instinct.aclose()

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/v1/models", models, methods=["GET"]),
        *[Route(p, routed, methods=["POST"]) for p in ROUTED_PATHS],
        Route("/pin/{d}/{rest:path}", pinned, methods=["GET", "POST"]),
        Route("/hydra/status", a_status, methods=["GET"]),
        Route("/hydra/explain", a_explain, methods=["POST"]),
        Route("/hydra/decisions", a_decisions, methods=["GET"]),
        Route("/hydra/reload", a_reload, methods=["POST"]),
        Route("/hydra/drain/{node}", a_drain, methods=["POST"]),
        Route("/hydra/undrain/{node}", a_undrain, methods=["POST"]),
        Route("/hydra/metrics", a_metrics, methods=["GET"]),
        Route("/{path:path}", catch_all, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]),
    ]
    hosts = trusted_hosts(hy.env.get("OPENBEAST_HYDRA_TRUSTED_HOSTS", ""))
    app = Starlette(routes=routes, lifespan=lifespan,
                    middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=hosts)])
    app.state.hydra = hy
    return app


def load_for_serve(env) -> tuple[core.Config, Path, bool]:
    p = config_path(env)
    if p.exists():
        return core.load_config(p, dict(env)), p, False
    cfg = core.implicit_config(dict(env))
    return cfg, p, True


def _cmd_check(arg: str, env, as_json: bool) -> int:
    p = Path(arg) if arg else config_path(env)
    try:
        if p.exists():
            cfg, what = core.load_config(p, dict(env)), str(p)
        elif arg:
            raise core.ConfigError([f"{p} does not exist"])
        else:
            cfg, what = core.implicit_config(dict(env)), f"implicit config ({p} absent)"
    except core.ConfigError as e:
        if as_json:
            print(json.dumps({"ok": False, "errors": e.errors, "warnings": e.warnings}))
        else:
            for w in e.warnings:
                print(f"WARNING: {w}", file=sys.stderr)
            for err in e.errors:
                print(f"ERROR: {err}", file=sys.stderr)
            print(f"hydra config INVALID: {len(e.errors)} error(s)", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps({"ok": True, "config": cfg.hash, "source": what, "warnings": cfg.warnings,
                          "nodes": len(cfg.nodes), "deployments": len(cfg.deployments),
                          "routes": len(cfg.routes), "classify_route": "classify" in cfg.routes}))
    else:
        for w in cfg.warnings:
            print(f"WARNING: {w}", file=sys.stderr)
        print(f"OK {cfg.hash}: {what} — {len(cfg.nodes)} node(s), {len(cfg.deployments)} deployment(s), "
              f"{len(cfg.routes)} route(s), {len(cfg.rules)} rule(s)")
    return 0


def _cmd_explain(doc: str, env) -> int:
    try:
        cfg, _, _ = load_for_serve(env)
    except core.ConfigError as e:
        print("\n".join(f"ERROR: {x}" for x in e.errors), file=sys.stderr)
        return 1
    try:
        body = json.loads(doc)
    except ValueError as e:
        print(f"ERROR: --explain takes a JSON object ({e})", file=sys.stderr)
        return 2
    state = core.FleetState(cfg)
    for hs in state.health.values():        # offline: assume every deployment is READY
        hs.h.state = core.READY
    for d in cfg.deployments.values():
        if d.conformance == "required":
            state.conformance[d.id] = ("pass", False)
    print(json.dumps(explain(cfg, state, body if isinstance(body, dict) else {}), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="beast-hydra — route OpenAI API traffic across engines")
    ap.add_argument("--check", nargs="?", const="", metavar="PATH", help="validate a config and exit")
    ap.add_argument("--json", action="store_true", help="with --check: machine-readable result")
    ap.add_argument("--explain", metavar="JSON", help="dry-run a request body (all deployments assumed READY)")
    ap.add_argument("--print-default-config", action="store_true", help="print the implicit config as TOML")
    ap.add_argument("--config", help="config path (default $OPENBEAST_HYDRA_CONFIG or ./hydra.toml)")
    ap.add_argument("--port", type=int, help="default $OPENBEAST_HYDRA_PORT or 8095 (always 127.0.0.1)")
    a = ap.parse_args(argv)
    env = dict(os.environ)
    if a.config:
        env["OPENBEAST_HYDRA_CONFIG"] = a.config
    if a.check is not None:
        return _cmd_check(a.check, env, a.json)
    if a.print_default_config:
        sys.stdout.write(core.to_toml(core.implicit_raw(env), header=(
            "# hydra.toml — generated by `python3 agents/hydra.py --print-default-config`.\n"
            "# The implicit single-node config: this rig's engine behind route `beast`.\n"
            "# Edit it (hydra.toml.example has the full schema), then: scripts/hydra.sh check\n")))
        return 0
    if a.explain is not None:
        return _cmd_explain(a.explain, env)
    return serve(env, a.port)


def serve(env, port: int | None = None) -> int:
    import uvicorn
    try:
        cfg, path, implicit = load_for_serve(env)
    except core.ConfigError as e:
        for x in e.errors:
            print(f"ERROR: {x}", file=sys.stderr)
        print("beast-hydra refuses to start on an invalid config (fail closed). "
              "Check it: scripts/hydra.sh check", file=sys.stderr)
        return 1
    port = port or int(env.get("OPENBEAST_HYDRA_PORT") or env.get("HYDRA_PORT") or 8095)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))        # loopback, never configurable
        sock.listen(128)
    except OSError as e:
        sock.close()
        print(f"ERROR: cannot bind 127.0.0.1:{port} ({e}) — is another beast-hydra running?", file=sys.stderr)
        return 1
    hy = Hydra(cfg, env=env, cfg_path=path, implicit=implicit)
    if implicit:
        _log(f"no {path} — using the implicit single-node config {cfg.hash} "
             f"(rig={cfg.nodes['rig'].url}, engine={cfg.nodes['rig'].engine})")
    for w in cfg.warnings:
        _log(f"WARNING: {w}")
    print(f"beast-hydra on http://127.0.0.1:{sock.getsockname()[1]}  config={cfg.hash}  "
          f"nodes={len(cfg.nodes)} routes={len(cfg.routes)}  inbound_auth="
          f"{'key' if hy.inbound_key else 'open'}", flush=True)
    app = create_app(hy)
    config = uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=5)
    uvicorn.Server(config).run(sockets=[sock])
    return 0


if __name__ == "__main__":
    sys.exit(main())
