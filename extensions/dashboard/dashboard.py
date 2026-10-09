#!/usr/bin/env python3
"""OpenBeast status dashboard — a read-only GPU / model / services view.

Stdlib only (no new dependency): http.server + subprocess + urllib. Gathers
live status from nvidia-smi, the model server (:8080), the identity tool server
(:3001, incl. its Prometheus /metrics), Open WebUI (:3000), and SearXNG (:8888),
and serves a self-refreshing HTML page plus a /api/status JSON endpoint.

Bind + port come from OPENBEAST_BIND / DASHBOARD_PORT (see run.sh). This is an
optional extension (extensions/dashboard) — the core stack does not depend on it.
"""
import json
import os
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_DIR = os.environ.get("OPENBEAST_REPO_DIR", ".")
BIND = os.environ.get("OPENBEAST_BIND", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.environ.get("DASHBOARD_PORT", "3002"))
# Probes always target loopback — the dashboard runs on the same box as the
# stack; BIND only controls who can reach the dashboard itself.
H = "127.0.0.1"

# The inference server (lib/conf.sh INFERENCE_*; docs/DGX_SPARK_PLAN.md).
# Unset — every rig before multi-backend support — is exactly the old
# behaviour: llama-server on loopback :8080. INFERENCE_URL is exported only
# when an operator set it (a vLLM / TensorFold cluster on the Sparks, or a
# llama-server on another box).
_BACKEND = os.environ.get("OPENBEAST_INFERENCE_BACKEND", "").strip().lower() or "llama"
if _BACKEND not in ("llama", "vllm", "tensorfold"):
    _BACKEND = "llama"
_INFER = (os.environ.get("OPENBEAST_INFERENCE_URL", "").strip().rstrip("/")
          or f"http://{H}:8080")


def _conf_slots():
    """INFERENCE_SLOTS: the concurrency a server that exposes none was
    launched with (vLLM --max-num-seqs, TensorFold --parallel), or None."""
    raw = os.environ.get("OPENBEAST_INFERENCE_SLOTS", "").strip()
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n > 0 else None


# Keyed llama-server (LLAMA_API_KEY): the dashboard's own probes must present
# the bearer. start.sh's extension launcher inherits the conf.sh export;
# a standalone `run.sh` on a keyless stack sends nothing, unchanged.
_API_KEY = os.environ.get("OPENBEAST_API_KEY", "").strip()
# beast-gate presence changes what "auth" means to a client (see slot_status).
_EDGE_GATE = os.environ.get("EDGE_GATE", "").strip().lower() == "true"
# ALLOW_ANON serves unregistered callers as a single "anon" device, so the
# gate is up but per-device identity is NOT in force. Saying "device" there
# would overstate what a client is actually protected by. It only applies
# while NO device is enrolled, though — see _edge_has_devices().
_EDGE_ANON = os.environ.get("OPENBEAST_EDGE_ALLOW_ANON", "").strip().lower() == "true"
# beast-instinct: conf.sh exports these only under INSTINCT=true.
_INSTINCT = os.environ.get("OPENBEAST_INSTINCT", "").strip().lower() == "true"
try:
    _INSTINCT_PORT = int(os.environ.get("OPENBEAST_INSTINCT_PORT", "") or 8094)
except ValueError:
    _INSTINCT_PORT = 8094


def _edge_has_devices():
    """Whether beast-gate's registry holds any enrolled device.

    The gate honours ALLOW_ANON only while its registry is empty
    (agents/edge.py _identify): the moment one device is enrolled, a keyless
    or unknown-key caller gets 401. Deriving "anon" from the env flag alone
    told clients to connect without a key into a guaranteed 401. Mirrors
    Registry.configured — any entry with a key hash counts, revoked or not.
    An unreadable or corrupt registry answers True: the gate then keeps its
    last good map, and claiming "anon" is the direction that misleads.
    """
    path = os.path.join(REPO_DIR, ".run", "clients.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    try:
        return any((d.get("key_sha256") or "").strip()
                   for d in data.get("devices", []))
    except (AttributeError, TypeError):
        return True


def _get(url, timeout=2, auth=False):
    try:
        req = urllib.request.Request(url)
        # TensorFold has no auth at all: never hand it the key.
        if auth and _API_KEY and _BACKEND != "tensorfold":
            req.add_header("Authorization", f"Bearer {_API_KEY}")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception:
        return None, ""


def gpu_status():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,"
             "utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        if not out:
            return None
        name, used, total, util, temp = [x.strip() for x in out.split(",")[:5]]
        used, total = int(used), int(total)
        return {"name": name, "used_mib": used, "total_mib": total,
                "free_mib": total - used, "util_pct": int(util), "temp_c": int(temp),
                "used_pct": round(100 * used / total) if total else 0}
    except Exception:
        return None


def model_status(health=None):
    ok = (health or _get(f"{_INFER}/health"))[0] == 200
    served = ""
    try:
        served = open(os.path.join(REPO_DIR, ".run", "serve-script")).read().strip()
    except Exception:
        pass
    alias = ""
    st, body = _get(f"{_INFER}/v1/models", auth=True)
    if st == 200:
        try:
            alias = json.loads(body)["data"][0].get("id", "")
        except Exception:
            pass
    return {"healthy": ok, "serve_script": served, "alias": alias}


def services_status(health=None):
    """Up/down per service. `health` is the model server's /health answer,
    (status, body), when the caller already has it — one status document then
    costs the inference server one /health, not one per section."""
    if health is None:
        health = _get(f"{_INFER}/health")
    svc = [
        ("model", None, "ok"),
        ("tools", f"http://{H}:3001/health", "ok"),
        ("webui", f"http://{H}:3000/api/version", "version"),
        ("search", f"http://{H}:8888/", ""),
    ]
    out = {}
    for name, url, needle in svc:
        st, body = health if url is None else _get(url)
        out[name] = bool(st and st < 500 and (needle in body if needle else True))
    if _BACKEND != "llama":
        # vLLM's /health is an EMPTY 200 and TensorFold's {"ok": true}: for
        # them the status code is the answer (a 404 is a wrong URL, not up).
        out["model"] = health[0] == 200
    if _INSTINCT:
        # beast-instinct (INSTINCT=true): a plain bool like its siblings — the
        # /health route is open and says nothing else. Absent when instinct is
        # off, so a default rig's /api/slot is byte-identical (v2 is additive:
        # a new key inside `services`, never a new top-level key).
        out["instinct"] = _get(f"http://{H}:{_INSTINCT_PORT}/health")[0] == 200
    return out


def tool_metrics():
    # Pull a few headline counters from the tool server's Prometheus text.
    st, body = _get(f"http://{H}:3001/metrics")
    if st != 200:
        return {}
    calls, errors = 0, 0
    for line in body.splitlines():
        if line.startswith("openbeast_tool_calls_total"):
            try:
                v = float(line.rsplit(" ", 1)[1])
                calls += v
                if 'outcome="error"' in line:
                    errors += v
            except Exception:
                pass
    return {"tool_calls": int(calls), "tool_errors": int(errors)}


def status():
    health = _get(f"{_INFER}/health")
    return {"gpu": gpu_status(), "model": model_status(health),
            "services": services_status(health), "metrics": tool_metrics()}


# The beast-slot contract version this rig publishes, and the oldest client
# contract it still serves. v2 is purely ADDITIVE over v1 — every v1 field is
# present under its v1 name — so a v1 client keeps working and MIN_CLIENT
# stays 1. Bump MIN_CLIENT only when a field is removed or its meaning changes.
SLOT_CONTRACT = 2
SLOT_MIN_CLIENT = 1


def _uncommented(path):
    """Read a shell script with comment lines stripped, or None if unreadable.

    serve.sh *documents* --kv-unified in prose as well as passing it; a flag
    that was deleted but still explained must not read as "in use".
    """
    try:
        with open(path) as fh:
            src = fh.read()
    except Exception:
        return None
    out = []
    for ln in src.splitlines():
        if ln.lstrip().startswith("#"):
            continue
        # Also drop TRAILING comments: an explanatory "# ... --no-kv-unified"
        # after real code would otherwise flip ctx_shared to False and make
        # ctx_total overstate the rig by the slot count. Naive on quoted '#',
        # which is fine — we only ever look for flag tokens here.
        head = ln.split("#", 1)[0]
        if head.strip():
            out.append(head)
    return "\n".join(out)


def _kv_unified():
    """True when llama-server was launched with --kv-unified, else False;
    None when we cannot tell.

    This build exposes no kv_unified field on /props, so we derive it from the
    launch path we recorded: .run/serve-script names the model script, which
    execs scripts/serve.sh (where the flag lives today). A model script that
    passes --no-kv-unified later on the command line wins — llama.cpp takes the
    last occurrence. Never guesses: capacity numbers built on a wrong answer
    are worse than no numbers at all.
    """
    try:
        served = open(
            os.path.join(REPO_DIR, ".run", "serve-script")).read().strip()
    except Exception:
        return None
    if not served or "/" in served:   # unrecorded, or not a plain script name
        return None
    model_txt = _uncommented(os.path.join(REPO_DIR, "scripts", served))
    base_txt = _uncommented(os.path.join(REPO_DIR, "scripts", "serve.sh"))
    # Both halves are required. Falling back to serve.sh alone would report a
    # confident True while the model script (which can pass --no-kv-unified,
    # and wins — llama.cpp takes the last occurrence) was unreadable. A wrong
    # capacity number is worse than an absent one.
    if model_txt is None or base_txt is None:
        return None
    texts = [model_txt, base_txt]
    if any("--no-kv-unified" in t for t in texts):
        return False
    return any("--kv-unified" in t for t in texts)


def _queue_deferred():
    """requests_deferred from llama-server's /metrics, or None.

    The real queue-depth signal: slots.busy counts only in-flight generations,
    so at -np 1 it reads 1 whether one request or fifty are waiting. Metrics
    are opt-in server-side (--metrics); without it /metrics answers 501 and we
    report null rather than pretending the queue is empty.
    """
    st, body = _get(f"{_INFER}/metrics", auth=True)
    if st != 200:
        return None
    for line in body.splitlines():
        if line.startswith("llamacpp:requests_deferred "):
            try:
                return int(float(line.rsplit(" ", 1)[1]))
            except Exception:
                return None
    return None


def slot_status():
    """The beast-slot discovery contract (docs/BEAST_SLOT.md), version 2.

    Read-only, published on the tailnet via setup-tailscale.sh --publish-slot.
    Slot-count-agnostic by design: clients must treat slots.total as data —
    a future multi-slot serving profile (or a fleet router) answers the same
    shape. Never includes prompt text or key material.

    v2 adds the `capacity` block because v1's numbers invite one specific
    misread: under --kv-unified every slot advertises the FULL n_ctx while all
    slots share ONE pool (llama.cpp/src/llama-context.cpp:286-288), so
    slots.total × model.ctx overstates the rig by the slot count. capacity
    states the real total budget, whether it is shared, and the queue depth
    that slots.busy cannot see.
    """
    if _BACKEND != "llama":
        return _slot_status_remote()
    health = _get(f"{_INFER}/health")
    ok = health[0] == 200
    model = {"id": None, "ctx": None}
    slots = {"total": None, "busy": None}
    st, body = _get(f"{_INFER}/v1/models", auth=True)
    if st == 200:
        try:
            model["id"] = json.loads(body)["data"][0].get("id") or None
        except Exception:
            pass
    st, body = _get(f"{_INFER}/props", auth=True)
    if st == 200:
        try:
            props = json.loads(body)
            slots["total"] = props.get("total_slots")
            model["id"] = model["id"] or props.get("model_alias") or None
            # ctx fallback for --no-slots rigs: /props carries the same
            # per-slot number (meta->slot_n_ctx) that /slots reports as n_ctx.
            model["ctx"] = (props.get("default_generation_settings")
                            or {}).get("n_ctx") or None
        except Exception:
            pass
    # /slots may be disabled (--no-slots) → busy stays null. Busy detection
    # covers both server generations: is_processing (current) / state != 0.
    st, body = _get(f"{_INFER}/slots", auth=True)
    if st == 200:
        try:
            data = json.loads(body)
            slots["busy"] = sum(
                1 for s in data
                if s.get("is_processing") or s.get("state", 0) != 0)
            slots["total"] = slots["total"] or len(data)
            for s in data:
                if s.get("n_ctx"):
                    model["ctx"] = s["n_ctx"]
                    break
        except Exception:
            pass
    # Capacity: model.ctx is what ONE slot advertises. Shared → that number is
    # already the whole budget; per-slot → multiply. Either input missing
    # leaves ctx_total null.
    shared = _kv_unified()
    ctx_total = None
    if model["ctx"]:
        if shared is True:
            ctx_total = model["ctx"]
        elif shared is False and slots["total"]:
            ctx_total = model["ctx"] * slots["total"]
    if slots["total"] is None:
        profile = "unknown"
    elif slots["total"] == 1:
        profile = "mtp-single-slot"
    else:
        profile = "batched-multi-slot"
    return {
        "beast_slot": SLOT_CONTRACT,
        "min_client": SLOT_MIN_CLIENT,
        "healthy": ok,
        "model": model,
        "slots": slots,
        "capacity": {
            "ctx_shared": shared,
            "ctx_total": ctx_total,
            "queue_deferred": _queue_deferred(),
            "serving_profile": profile,
        },
        "services": services_status(health),
        "auth": _auth_mode(),
    }


def _auth_mode():
    # Gate-aware: with EDGE_GATE=true remote clients need a per-DEVICE
    # key even when LLAMA_API_KEY is unset. Reporting "open" there told
    # clients the opposite of the truth.
    return ("anon" if (_EDGE_GATE and _EDGE_ANON and not _edge_has_devices())
            else "device" if _EDGE_GATE
            else "key" if _API_KEY else "open")


def _prom(body, name):
    """Every sample of one Prometheus metric (any label set), as floats.

    vLLM labels its gauges per engine and model_name
    (`vllm:num_requests_running{engine="0",model_name="m"} 1.0`), so a
    bare-name prefix match is not enough and there may be several samples.
    """
    out = []
    for line in body.splitlines():
        if not line.startswith(name):
            continue
        rest = line[len(name):]
        if rest[:1] not in ("{", " "):
            continue            # a longer name that merely shares the prefix
        try:
            out.append(float(line.rsplit(" ", 1)[1]))
        except (IndexError, ValueError):
            pass
    return out


def _vllm_metrics():
    """(running, waiting, kv_usage) from vLLM's /metrics; each None when
    absent. running/waiting are summed across engines; kv_usage is the
    fullest engine's fraction (0..1 — "perc" in the name notwithstanding,
    vllm/v1/metrics/loggers.py: "1 means 100 percent usage"). /metrics is
    unauthenticated on vLLM even under --api-key; the key is sent anyway."""
    st, body = _get(f"{_INFER}/metrics", auth=True)
    if st != 200:
        return None, None, None
    run = _prom(body, "vllm:num_requests_running")
    wait = _prom(body, "vllm:num_requests_waiting")
    kv = (_prom(body, "vllm:kv_cache_usage_perc")
          or _prom(body, "vllm:gpu_cache_usage_perc"))   # pre-V1 name
    return (int(sum(run)) if run else None,
            int(sum(wait)) if wait else None,
            max(kv) if kv else None)


def _slot_status_remote():
    """/api/slot for a vLLM or TensorFold server (INFERENCE_BACKEND).

    Same contract version, same fields, plus two that only appear here:
    top-level `backend` (absent = llama, which keeps a llama rig's answer
    byte-identical) and `capacity.kv_usage`. Neither server exposes its
    concurrency, so slots.total is INFERENCE_SLOTS from the conf.

    vLLM: model.ctx is /v1/models max_model_len (the per-request window);
    busy/queue/kv_usage come from /metrics. PagedAttention draws every
    sequence from ONE block pool, so ctx_shared is true — but the pool's
    size in tokens is not something we can read without guessing, so
    ctx_total stays null (never a guessed budget, as for llama).
    TensorFold: /health and /v1/models only, so capacity is null.
    """
    health = _get(f"{_INFER}/health")
    ok = health[0] == 200
    model = {"id": None, "ctx": None}
    slots = {"total": _conf_slots(), "busy": None}
    capacity = {"ctx_shared": None, "ctx_total": None, "queue_deferred": None,
                "kv_usage": None}
    st, body = _get(f"{_INFER}/v1/models", auth=True)
    if st == 200:
        try:
            first = json.loads(body)["data"][0]
            model["id"] = first.get("id") or None
            ctx = first.get("max_model_len")
            model["ctx"] = ctx if isinstance(ctx, int) and ctx > 0 else None
        except Exception:
            pass
    if _BACKEND == "vllm":
        capacity["ctx_shared"] = True
        running, waiting, kv = _vllm_metrics()
        slots["busy"] = running
        capacity["queue_deferred"] = waiting
        capacity["kv_usage"] = kv
    total = slots["total"]
    capacity["serving_profile"] = ("unknown" if total is None
                                   else "single-slot" if total == 1
                                   else "batched-multi-slot")
    return {
        "beast_slot": SLOT_CONTRACT,
        "min_client": SLOT_MIN_CLIENT,
        "healthy": ok,
        "backend": _BACKEND,
        "model": model,
        "slots": slots,
        "capacity": capacity,
        "services": services_status(health),
        "auth": _auth_mode(),
    }


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>OpenBeast Status</title><meta name=viewport content="width=device-width,initial-scale=1">
<style>
 body{background:#0c0f14;color:#d6dbe4;font:14px/1.5 system-ui,sans-serif;margin:0;padding:24px}
 h1{font-size:20px;margin:0 0 4px}.sub{color:#7c8598;margin-bottom:20px}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}
 .card{background:#141922;border:1px solid #232b38;border-radius:10px;padding:16px}
 .card h2{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:#7c8598;margin:0 0 12px}
 .big{font-size:26px;font-weight:600}.row{display:flex;justify-content:space-between;padding:3px 0}
 .k{color:#7c8598}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px}
 .up{background:#3fb950}.down{background:#f85149}
 .bar{height:8px;background:#232b38;border-radius:4px;overflow:hidden;margin-top:8px}
 .bar>i{display:block;height:100%;background:linear-gradient(90deg,#3fb950,#d29922)}
 a{color:#58a6ff}
</style></head><body>
<h1>🦁 OpenBeast Status</h1><div class=sub id=ts>loading…</div>
<div class=grid id=grid></div>
<script>
async function tick(){
 let s; try{s=await (await fetch('/api/status')).json()}catch(e){return}
 const g=s.gpu,m=s.model,sv=s.services,mt=s.metrics||{};
 const svc=Object.entries(sv).map(([k,v])=>`<div class=row><span><span class="dot ${v?'up':'down'}"></span>${k}</span><span class=k>${v?'up':'down'}</span></div>`).join('');
 const gpu=g?`<div class=big>${g.used_pct}% <span class=k style=font-size:14px>${(g.used_mib/1024).toFixed(1)}/${(g.total_mib/1024).toFixed(0)} GB</span></div>
   <div class=bar><i style=width:${g.used_pct}%></i></div>
   <div class=row><span class=k>free</span><span>${(g.free_mib/1024).toFixed(1)} GB</span></div>
   <div class=row><span class=k>GPU util</span><span>${g.util_pct}%</span></div>
   <div class=row><span class=k>temp</span><span>${g.temp_c}°C</span></div>
   <div class=row><span class=k>card</span><span>${g.name}</span></div>`:'<div class=k>no GPU detected</div>';
 document.getElementById('grid').innerHTML=`
  <div class=card><h2>GPU / VRAM</h2>${gpu}</div>
  <div class=card><h2>Model</h2>
    <div class=big style=font-size:18px><span class="dot ${m.healthy?'up':'down'}"></span>${m.alias||m.serve_script||'—'}</div>
    <div class=row><span class=k>serve script</span><span>${m.serve_script||'—'}</span></div>
    <div class=row><span class=k>API</span><span>${m.healthy?':8080 healthy':'down'}</span></div></div>
  <div class=card><h2>Services</h2>${svc}</div>
  <div class=card><h2>Tool activity</h2>
    <div class=big>${mt.tool_calls??'—'}</div><div class=k>total tool calls</div>
    <div class=row><span class=k>errors</span><span>${mt.tool_errors??'—'}</span></div>
    <div class=row><span class=k>raw</span><span><a href=http://localhost:3001/metrics>:3001/metrics</a></span></div></div>`;
 document.getElementById('ts').textContent='updated '+new Date().toLocaleTimeString();
}
tick();setInterval(tick,3000);
</script></body></html>"""


# /api/slot is published to the tailnet unauthenticated (--publish-slot) and
# one uncached answer costs eight upstream requests (plus an nvidia-smi for
# /api/status). Left as it was, a peer polling in a loop multiplied its own
# rate by that against llama-server, WebUI and SearXNG, on one thread per
# request. So: one gather per CACHE_S however many callers ask, and a hard cap
# on handler threads. Clients poll in seconds; a second of staleness is noise.
CACHE_S = 1.5
MAX_HANDLERS = 16
# A client that connects and then says nothing gives its thread back.
SOCKET_TIMEOUT_S = 10


class _Cached:
    """fn()'s answer, recomputed at most once per `ttl` seconds.

    The lock is held across the gather on purpose: callers that arrive during
    one wait for it and share its answer instead of starting their own.
    """

    def __init__(self, fn, ttl=CACHE_S):
        self._fn, self._ttl = fn, ttl
        self._lock = threading.Lock()
        self._at = None
        self._val = None

    def get(self):
        with self._lock:
            now = time.monotonic()
            if self._at is None or now - self._at >= self._ttl:
                self._val = self._fn()
                self._at = time.monotonic()
            return self._val


# Late-bound, so the functions stay the uncached source of truth (and a test
# that swaps one is seen).
_slot_cached = _Cached(lambda: slot_status())
_status_cached = _Cached(lambda: status())


class Handler(BaseHTTPRequestHandler):
    timeout = SOCKET_TIMEOUT_S

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        if self.path.startswith("/api/slot"):
            body = json.dumps(_slot_cached.get()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path.startswith("/api/status"):
            body = json.dumps(_status_cached.get()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path in ("/", "/index.html"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_BUSY = (b"HTTP/1.0 503 Service Unavailable\r\nRetry-After: 1\r\n"
         b"Content-Type: text/plain\r\nContent-Length: 5\r\nConnection: close\r\n\r\nbusy\n")


class BoundedServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with at most MAX_HANDLERS handler threads.

    A connection beyond the cap is answered 503 from the accept loop and
    closed: no thread is started for it, so a flood cannot grow the process.
    """

    def __init__(self, *a, max_handlers=MAX_HANDLERS, **kw):
        super().__init__(*a, **kw)
        self._free = threading.BoundedSemaphore(max_handlers)

    def process_request(self, request, client_address):
        if not self._free.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(_BUSY)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._free.release()        # the thread never started
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._free.release()


if __name__ == "__main__":
    print(f"OpenBeast dashboard on http://{BIND}:{PORT}", flush=True)
    BoundedServer((BIND, PORT), Handler).serve_forever()
