#!/usr/bin/env python3
"""OpenBeast status dashboard: a read-only view of the command center.

Stdlib only (no new dependency): http.server + subprocess + urllib. Routes:

  /               the page (static/: index.html, app.css, app.js, Geist fonts)
  /api/status     this node: model, serving, gate, services, tools, events
  /api/cluster    this node and every peer (DASHBOARD_PEERS / cluster.env)
  /api/slot       the beast-slot contract (docs/BEAST_SLOT.md), unchanged

  /api/power      the power chart series for one window (?window=15m|1h|6h|24h)

GET only. /api/status is a bounded, cached upstream gather (HTTP probes of
the stack, at most GATHER_DEADLINE_S); its `gpu` block comes from the head
sampler, so a request never runs nvidia-smi. Everything that needs a clock
or another machine (peer metrics over ssh, token and request rates, power,
history) runs on background samplers started in __main__: /api/cluster and
/api/power only read their latest snapshot.

Bind + port come from OPENBEAST_BIND / DASHBOARD_PORT (see run.sh). This is an
optional extension (extensions/dashboard) — the core stack does not depend on it.
"""
import calendar
import collections
import contextlib
import importlib.util
import json
import math
import os
import re
import signal
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _load_hostpolicy():
    """The one Host-header allowlist every OpenBeast HTTP surface shares
    (agents/hostpolicy.py, stdlib only), loaded by path from this checkout —
    not via sys.path, which would let agents/*.py shadow a stdlib module."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "..", "agents", "hostpolicy.py")
    spec = importlib.util.spec_from_file_location("openbeast_hostpolicy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_hostpolicy = _load_hostpolicy()
host_allowed = _hostpolicy.host_allowed
is_ip_literal = _hostpolicy.is_ip_literal
trusted_hosts = _hostpolicy.trusted_hosts

REPO_DIR = os.environ.get("OPENBEAST_REPO_DIR", ".")
BIND = os.environ.get("OPENBEAST_BIND", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.environ.get("DASHBOARD_PORT", "3002"))
# Probes target the stack on this box; BIND only controls who can reach the
# dashboard itself. The stack binds BIND_HOST, and a socket bound to a specific
# LAN or tailnet address refuses loopback — so dial what conf.sh worked out
# (OPENBEAST_PROBE_HOST, URL-ready: IPv6 comes bracketed), which start.sh's
# extension launcher passes down. Unset — a standalone run.sh — is loopback.
_LOOP = "127.0.0.1"
H = os.environ.get("OPENBEAST_PROBE_HOST", "").strip() or _LOOP

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



# DNS rebinding: a page the operator opens on the rig can re-point its own
# name at 127.0.0.1 and read the rig-local routes (the page, /api/status) as
# same-origin. A loopback bind does not stop that; a Host pin does. The same
# list beast-chat, beast-artifact and the tool server answer to (loopback,
# this machine's name, *.ts.net), plus any IP literal: rebinding needs a
# NAME, and a literal is answered by whatever owns it (us, on a LAN or
# tailnet BIND). Extra names: OPENBEAST_DASHBOARD_ALLOWED_HOSTS (or the
# bare DASHBOARD_ALLOWED_HOSTS), comma-separated.
ALLOWED_HOSTS = _hostpolicy.trusted_hosts(",".join(filter(None, (
    os.environ.get("OPENBEAST_DASHBOARD_ALLOWED_HOSTS", ""),
    os.environ.get("DASHBOARD_ALLOWED_HOSTS", "")))))   # the bare name, too


def host_ok(values):
    """True when the request's Host header(s) may see a rig-local route."""
    if len(values) != 1:
        return False                 # none, or two answers to "which host"
    return (_hostpolicy.host_allowed(values[0], ALLOWED_HOSTS)
            or _hostpolicy.is_ip_literal(values[0]))


def _endpoint(url):
    """host:port of the inference URL, for the page ("127.0.0.1:8000")."""
    rest = url.split("//", 1)[-1].split("/", 1)[0]
    rest = rest.rsplit("@", 1)[-1]   # never a userinfo part
    if rest.endswith("]") or ":" not in rest:
        rest += ":443" if url.startswith("https://") else ":80"
    return rest


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
# while NO registry has ever existed, though — see _edge_has_devices().
_EDGE_ANON = os.environ.get("OPENBEAST_EDGE_ALLOW_ANON", "").strip().lower() == "true"
# beast-instinct: conf.sh exports these only under INSTINCT=true.
_INSTINCT = os.environ.get("OPENBEAST_INSTINCT", "").strip().lower() == "true"
try:
    _INSTINCT_PORT = int(os.environ.get("OPENBEAST_INSTINCT_PORT", "") or 8094)
except ValueError:
    _INSTINCT_PORT = 8094


def _edge_has_devices():
    """Whether beast-gate has ever had a device registry — so ALLOW_ANON is off.

    The gate honours ALLOW_ANON only on a rig that has NEVER had a registry
    (agents/edge.py Registry.exists): once .run/clients.json exists — even
    emptied by `clients.sh remove`, even corrupt — or has existed (the
    .run/clients.enrolled marker beside it), a keyless or unknown-key caller
    gets 401. Mirrors Registry.exists, not the older `configured` rule,
    which advertised "anon" for an emptied file into a guaranteed 401.
    """
    run = os.path.join(REPO_DIR, ".run")
    for name in ("clients.json", "clients.enrolled"):
        try:
            os.stat(os.path.join(run, name))
            return True
        except FileNotFoundError:
            continue
        except OSError:
            return True
    return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Status probes never follow a 3xx.

    urllib's default redirect handler copies every add_header() header onto
    the redirected request, cross-host included, so a keyed probe of a
    remote INFERENCE_URL that answered `302 Location: http://elsewhere/`
    handed LLAMA_API_KEY to elsewhere on every 1.5 s poll (review 2026-11-16).
    A health probe should report what the address itself says: a redirect
    reads as no answer (urllib raises HTTPError, _get returns (None, "")).
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _get(url, timeout=2, auth=False):
    try:
        req = urllib.request.Request(url)
        if auth and _API_KEY:
            # Unredirected as well: belt and braces should a redirect-following
            # opener ever be swapped back in.
            req.add_unredirected_header("Authorization", f"Bearer {_API_KEY}")
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception:
        return None, ""


# One status document asks up to nine upstreams. Asked one after another,
# each with its own 2 s timeout, a single upstream that accepts and then says
# nothing (a wedged or SIGSTOPped llama-server) cost 10 s of it, and the
# cache lock made every caller wait that long: the client's 5 s curl gave up
# and reported the slot API "not published" — hiding the healthy:false it
# exists to show. Now every probe of one document runs at once, and the
# document is built from whatever answered within GATHER_DEADLINE_S; a probe
# still out then reads as no answer (down / null), never as a hang.
GATHER_DEADLINE_S = 3.0
_memo = threading.local()


class _Probe:
    """One in-flight probe of a (url, auth) key, shared by every gather that
    wants that key while it is out."""

    __slots__ = ("done", "result")

    def __init__(self):
        self.done = threading.Event()
        self.result = (None, "")


# (url, auth) -> the _Probe still out for it. Shared, not skipped: /api/slot
# and /api/status gather under separate _Cached locks and ask the same keys
# (/health, /v1/models, :3001, :3000, :8888). The old rule served a key that
# was already in flight as "no answer", so whichever gather started second
# reported a healthy model DOWN (healthy:false, model.id null) and cached it
# for CACHE_S (review 2026-11-16). Now the second gather waits on the same
# probe, up to its own deadline. A straggler that outlives every deadline is
# still one thread per URL.
_inflight = {}
_inflight_lock = threading.Lock()


def _run_probe(key, probe):
    try:
        probe.result = _get(key[0], auth=key[1])
    finally:
        with _inflight_lock:
            if _inflight.get(key) is probe:
                del _inflight[key]
        probe.done.set()


def _fetch(url, auth=False):
    """_get, answered from the current _prefetched() batch when it is there."""
    answers = getattr(_memo, "answers", None)
    if answers is not None and (url, auth) in answers:
        return answers[(url, auth)]
    return _get(url, auth=auth)


@contextlib.contextmanager
def _prefetched(reqs, deadline=None):
    """Probe every (url, auth) in `reqs` concurrently, bounded by `deadline`
    overall, and serve those answers to _fetch for the duration. A key some
    other gather is already probing is joined, not re-asked and not skipped."""
    deadline = GATHER_DEADLINE_S if deadline is None else deadline
    prev = getattr(_memo, "answers", None)
    # Nested inside another gather (/api/status builds the slot document in
    # its own batch): a key the outer batch already answered is reused, so one
    # status document still costs the inference server ONE /health.
    known = {k: prev[k] for k in reqs if prev is not None and k in prev}
    reqs = [k for k in dict.fromkeys(reqs) if k not in known]
    probes = {}
    for k in reqs:
        with _inflight_lock:
            p = _inflight.get(k)
            fresh = p is None
            if fresh:
                p = _inflight[k] = _Probe()
        if fresh:
            threading.Thread(target=_run_probe, args=(k, p), daemon=True).start()
        probes[k] = p
    end = time.monotonic() + deadline
    for p in probes.values():
        p.done.wait(max(0.0, end - time.monotonic()))
    # A probe still out at the deadline reads as no answer (down / null).
    answers = dict(prev or {})
    answers.update({k: (p.result if p.done.is_set() else (None, ""))
                    for k, p in probes.items()})
    _memo.answers = answers
    try:
        yield
    finally:
        _memo.answers = prev


def _service_requests():
    reqs = [(f"http://{H}:3001/health", False), (f"http://{H}:3000/api/version", False),
            (f"http://{H}:8888/", False)]
    if _INSTINCT:
        reqs.append((f"http://{_LOOP}:{_INSTINCT_PORT}/health", False))
    return reqs


def _slot_requests():
    reqs = [(f"{_INFER}/health", False), (f"{_INFER}/v1/models", True)]
    if _BACKEND == "llama":
        reqs += [(f"{_INFER}/props", True), (f"{_INFER}/slots", True),
                 (f"{_INFER}/metrics", True)]
    elif _BACKEND == "vllm":
        reqs.append((f"{_INFER}/metrics", True))
    return reqs + _service_requests()


def gpu_status():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,"
             "utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2).stdout.strip()
        return _parse_gpus(out)
    except Exception:
        return None


def _smi_int(v):
    """One nvidia-smi field as an int; None for "[N/A]" / "[Not Supported]".

    A unified-memory GPU (the DGX Spark's GB10) has no VRAM of its own and
    answers memory.used / memory.total with the literal "[N/A]". int() on
    that raised, and the whole GPU card read "no GPU detected" on a rig whose
    name, utilisation and temperature were all readable.
    """
    try:
        f = float(v)
        return int(f) if math.isfinite(f) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _meminfo_mib(path="/proc/meminfo"):
    """(used, total) host RAM in MiB, the pool a unified-memory GPU draws
    from (MemTotal - MemAvailable, as lib/hardware.sh sizes it), or None."""
    try:
        vals = {}
        with open(path) as f:
            for ln in f:
                k, _, rest = ln.partition(":")
                if k in ("MemTotal", "MemAvailable"):
                    vals[k] = int(rest.split()[0]) // 1024
        total, avail = vals["MemTotal"], vals["MemAvailable"]
    except (OSError, KeyError, ValueError, IndexError):
        return None
    return (total - avail, total) if total > 0 else None


def _parse_gpus(out, meminfo=None):
    """nvidia-smi's csv rows -> the one `gpu` object /api/status serves.

    One row per card. Several cards fold into the same shape: memory summed
    (llama.cpp splits a model across them), utilisation and temperature the
    busiest / hottest card. Splitting the whole output on commas glued row
    one's temperature to row two's name and reported "no GPU detected" on
    every multi-GPU host.

    A field the card does not report ("[N/A]") is that field missing, not
    the GPU: a unified-memory card's memory is sized from host RAM instead
    (`unified: true`), and with no number at all the memory fields are null.
    A row with no readable number at all is not a GPU row.
    """
    cards = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 5:
            raise ValueError(f"short nvidia-smi row: {line!r}")
        name, nums = parts[0], [_smi_int(x) for x in parts[1:5]]
        if not name or all(n is None for n in nums):
            continue            # not a GPU row at all
        cards.append((name, *nums))
    if not cards:
        return None
    names = [c[0] for c in cards]
    if len(cards) == 1:
        name = names[0]
    elif len(set(names)) == 1:
        name = f"{len(cards)}x {names[0]}"
    else:
        name = " + ".join(names)

    def fold(i, how):
        # A field one card cannot report makes the fold unknown, not a
        # smaller number: half a memory total is worse than none.
        vals = [c[i] for c in cards]
        return None if any(v is None for v in vals) else how(vals)

    # Unified memory (GB10): no card reports VRAM, so the pool is host RAM.
    # Only some cards short of memory is a fold we cannot make: unknown.
    unified = all(c[1] is None and c[2] is None for c in cards)
    if unified:
        used, total = (meminfo or _meminfo_mib)() or (None, None)
    else:
        used, total = fold(1, sum), fold(2, sum)
    have_mem = used is not None and bool(total)
    gpu = {"name": name, "used_mib": used, "total_mib": total,
           "free_mib": total - used if have_mem else None,
           "util_pct": fold(3, max), "temp_c": fold(4, max),
           "used_pct": round(100 * used / total) if have_mem else None}
    if unified:
        gpu["unified"] = True
    return gpu


def _model_up(health):
    """The one verdict on the model server, from its /health answer
    (status, body): a 200, and for TensorFold not a stalled pair (rank 0's
    iteration running past TF_STALL_S while /health still says ok). The
    page, the services row, /api/slot and the event log all use this, so a
    frozen pair can no longer read "Serving · healthy" beside a slot
    document that says healthy:false (review 2026-11-22)."""
    if not health or health[0] != 200:
        return False
    return not (_BACKEND == "tensorfold" and _tf_health(health[1]).get("stalled"))


def model_status(health=None):
    ok = _model_up(health or _fetch(f"{_INFER}/health"))
    served = ""
    try:
        served = open(os.path.join(REPO_DIR, ".run", "serve-script")).read().strip()
    except Exception:
        pass
    alias = ""
    st, body = _fetch(f"{_INFER}/v1/models", auth=True)
    if st == 200:
        try:
            alias = json.loads(body)["data"][0].get("id", "")
        except Exception:
            pass
    return {"healthy": ok, "serve_script": served, "alias": alias,
            "backend": _BACKEND, "endpoint": _endpoint(_INFER)}


def services_status(health=None):
    """Up/down per service. `health` is the model server's /health answer,
    (status, body), when the caller already has it — one status document then
    costs the inference server one /health, not one per section."""
    if health is None:
        health = _fetch(f"{_INFER}/health")
    svc = [
        ("model", None, "ok"),
        ("tools", f"http://{H}:3001/health", "ok"),
        ("webui", f"http://{H}:3000/api/version", "version"),
        ("search", f"http://{H}:8888/", ""),
    ]
    out = {}
    for name, url, needle in svc:
        st, body = health if url is None else _fetch(url)
        out[name] = bool(st and st < 500 and (needle in body if needle else True))
    if _BACKEND != "llama":
        # vLLM's /health is an EMPTY 200 and TensorFold's {"ok": true}: for
        # them the status code is the answer (a 404 is a wrong URL, not up);
        # a stalled TensorFold pair is down (_model_up).
        out["model"] = _model_up(health)
    if _INSTINCT:
        # beast-instinct (INSTINCT=true): a plain bool like its siblings — the
        # /health route is open and says nothing else. Absent when instinct is
        # off, so a default rig's /api/slot is byte-identical (v2 is additive:
        # a new key inside `services`, never a new top-level key).
        # Always loopback: instinct never follows BIND_HOST (start.sh).
        out["instinct"] = _fetch(f"http://{_LOOP}:{_INSTINCT_PORT}/health")[0] == 200
    return out


def tool_metrics(top=False):
    # Pull a few headline counters from the tool server's Prometheus text.
    st, body = _fetch(f"http://{H}:3001/metrics")
    if st != 200:
        return {}
    extra = {"top_tools": tool_breakdown(body)} if top else {}
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
    return {"tool_calls": int(calls), "tool_errors": int(errors), **extra}


def status():
    """/api/status: the v1 keys (gpu, model, services, metrics) unchanged, plus
    the v2 detail the page shows — serving (the slot document's numbers and
    the sampler's token rates), gate, service_list, host, events.

    `gpu` is the head sampler's latest reading (_v1_gpu): a request never
    runs nvidia-smi, so a slow or hung one can no longer hold this document
    past the page's fetch abort (review 2026-11-22). Without a head sampler
    (a test, an import) it falls back to one bounded nvidia-smi."""
    gpu = {}
    smi = None
    if _head is not None:
        gpu["v"] = _v1_gpu(_head.snapshot().get("gpu"))
    else:
        smi = threading.Thread(target=lambda: gpu.update(v=gpu_status()), daemon=True)
        smi.start()
    t0 = time.monotonic()
    reqs = ([(f"{_INFER}/health", False), (f"{_INFER}/v1/models", True),
             (f"http://{H}:3001/metrics", False)] + _service_requests()
            + _slot_requests() + _extra_service_requests())
    with _prefetched(reqs):
        health = _fetch(f"{_INFER}/health")
        svc = services_status(health)
        out = {"model": model_status(health), "services": svc,
               "metrics": tool_metrics(top=True)}
        slot = slot_status()            # nested: reuses this batch's answers
        live = _serve.snapshot() if _serve else {}
        gate = live.get("gate") or {"enabled": _EDGE_GATE}
        out["service_list"] = service_list(svc, gate)
    cap = slot.get("capacity") or {}
    out["model"].update({"id": (slot.get("model") or {}).get("id"),
                         "ctx": (slot.get("model") or {}).get("ctx"),
                         "engine": ENGINE_LABEL.get(_BACKEND, _BACKEND),
                         "unit": live.get("unit")})
    out["serving"] = {"slots": slot.get("slots"), "capacity": cap,
                      "requests_running": (slot.get("slots") or {}).get("busy"),
                      "requests_waiting": cap.get("queue_deferred"),
                      "kv_usage": cap.get("kv_usage"),
                      "admission": slot.get("admission"),
                      "tok_s": live.get("tok_s"), "tok_s_source": live.get("tok_s_source"),
                      "prompt_tok_s": live.get("prompt_tok_s"),
                      "history": live.get("history") or {"tok_s": []},
                      "interval_s": live.get("interval_s", SERVE_SAMPLE_S)}
    out["gate"] = gate
    out["auth"] = slot.get("auth")
    out["host"] = {"name": _head.name if _head else _head_name()}
    out["events"] = events()
    out["generated_at"] = round(time.time())
    if smi is not None:             # nvidia-smi has its own 2 s timeout
        smi.join(max(0.1, GATHER_DEADLINE_S - (time.monotonic() - t0)))
    return {"gpu": gpu.get("v"), **out}


def _v1_gpu(g):
    """The head sampler's node gpu view in the v1 /api/status `gpu` shape
    (name, used/total/free MiB, util, temp, used %), plus power_w (additive:
    GPU power.draw, W). None when the node reads no GPU."""
    if not g:
        return None
    used, total = g.get("mem_used_mib"), g.get("mem_total_mib")
    have = used is not None and bool(total)
    name = g.get("name") or ""
    if (g.get("count") or 1) > 1 and " + " not in name:
        name = f"{g['count']}x {name}"
    out = {"name": name, "used_mib": used, "total_mib": total,
           "free_mib": total - used if have else None,
           "util_pct": g.get("util_pct"), "temp_c": g.get("temp_c"),
           "used_pct": round(100 * used / total) if have else None,
           "power_w": g.get("power_w")}
    if g.get("unified"):
        out["unified"] = True
    return out


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
    st, body = _fetch(f"{_INFER}/metrics", auth=True)
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
    with _prefetched(_slot_requests()):
        if _BACKEND != "llama":
            return _slot_status_remote()
        return _slot_status_llama()


def _slot_status_llama():
    health = _fetch(f"{_INFER}/health")
    ok = health[0] == 200
    model = {"id": None, "ctx": None}
    slots = {"total": None, "busy": None}
    st, body = _fetch(f"{_INFER}/v1/models", auth=True)
    if st == 200:
        try:
            model["id"] = json.loads(body)["data"][0].get("id") or None
        except Exception:
            pass
    st, body = _fetch(f"{_INFER}/props", auth=True)
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
    st, body = _fetch(f"{_INFER}/slots", auth=True)
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
        **_admission_field(),
    }


def _edge_admission():
    """beast-gate's per-device limits, for a client to size its parallelism
    to, or None with the gate off (keeping a gate-off answer byte-identical).
    The same rule as agents/edge.py default_max_inflight: EDGE_MAX_INFLIGHT,
    else INFERENCE_SLOTS, else 2. Review 2026-11-16: slots.total said 4 on the
    Spark pair while one device was admitted 2, so a client running 4 agents
    learned of the cap only from 429s."""
    if not _EDGE_GATE:
        return None

    def _int(name):
        raw = os.environ.get(name, "").strip()
        return int(raw) if raw.isdigit() else None

    explicit, slots = _int("OPENBEAST_EDGE_MAX_INFLIGHT"), _int("OPENBEAST_INFERENCE_SLOTS")
    if explicit is not None:
        inflight = max(1, explicit)              # the gate's Bucket floors at 1
    elif slots:
        inflight = slots
    else:
        inflight = 2
    rate = _int("OPENBEAST_EDGE_RATE_LIMIT") or 120
    return {"per_device_inflight": inflight, "per_device_rate_per_min": rate}


def _admission_field():
    a = _edge_admission()
    return {"admission": a} if a else {}


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
            v = float(line.rsplit(" ", 1)[1])
        except (IndexError, ValueError):
            continue
        if math.isfinite(v):          # a NaN / Inf sample is no sample
            out.append(v)
    return out


def _vllm_metrics():
    """(running, waiting, kv_usage) from vLLM's /metrics; each None when
    absent. running/waiting are summed across engines; kv_usage is the
    fullest engine's fraction (0..1 — "perc" in the name notwithstanding,
    vllm/v1/metrics/loggers.py: "1 means 100 percent usage"). /metrics is
    unauthenticated on vLLM even under --api-key; the key is sent anyway."""
    st, body = _fetch(f"{_INFER}/metrics", auth=True)
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
    TensorFold: its /health body carries what vLLM needs /metrics for —
    context_length (model.ctx), requests_running (busy), streams.max (slots
    when INFERENCE_SLOTS is unset) and the ONE token pool every stream draws
    on (pool_tokens -> ctx_total, pool_free_tokens -> kv_usage). Its
    iteration_s is how long rank 0's current iteration has run: past
    TF_STALL_S the pair is stalled (a frozen or rebooting peer rank) while
    /health still says ok, so the model reads as down.
    """
    health = _fetch(f"{_INFER}/health")
    ok = _model_up(health)
    tf = _tf_health(health[1]) if _BACKEND == "tensorfold" and health[0] == 200 else {}
    model = {"id": None, "ctx": None}
    slots = {"total": _conf_slots(), "busy": None}
    capacity = {"ctx_shared": None, "ctx_total": None, "queue_deferred": None,
                "kv_usage": None}
    st, body = _fetch(f"{_INFER}/v1/models", auth=True)
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
    elif tf:
        if model["ctx"] is None:
            model["ctx"] = tf.get("ctx")
        if slots["total"] is None:
            slots["total"] = tf.get("streams")
        slots["busy"] = tf.get("busy")
        if tf.get("pool"):
            capacity["ctx_shared"] = True
            capacity["ctx_total"] = tf["pool"]
            capacity["kv_usage"] = tf.get("kv")
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
        **_admission_field(),
    }


# rank 0's current iteration running longer than this = a stalled pair. A
# normal iteration is milliseconds (a long-prompt prefill chunk, seconds);
# the recipe's own stall report fires at 300 (TF_GLM_MULTI_WATCHDOG_S).
TF_STALL_S = 120.0


def _tf_health(body):
    """The fields /api/slot reads from a TensorFold /health body; a field
    that is missing or not a sane number is simply absent (null upstream),
    never a guess."""
    try:
        h = json.loads(body)
    except Exception:
        return {}
    if not isinstance(h, dict):
        return {}

    def num(v, lo=0):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= lo else None

    out = {}
    ctx = num(h.get("context_length"), 1)
    if isinstance(ctx, int):
        out["ctx"] = ctx
    busy = num(h.get("requests_running"))
    if isinstance(busy, int):
        out["busy"] = busy
    streams = h.get("streams") if isinstance(h.get("streams"), dict) else {}
    smax = num(streams.get("max"), 1)
    if isinstance(smax, int):
        out["streams"] = smax
    pool, free = num(h.get("pool_tokens"), 1), num(h.get("pool_free_tokens"))
    if isinstance(pool, int):
        out["pool"] = pool
        if free is not None and free <= pool:
            out["kv"] = round(1 - free / pool, 4)
    it = num(h.get("iteration_s"))
    out["stalled"] = it is not None and it > TF_STALL_S
    return out


# ───────────────────────── v2: cluster, serving, gate, history ─────────────────────────
#
# Everything below is READ-ONLY and runs on background timers, never on an
# HTTP request: a request only reads the latest snapshot. Peer metrics come
# from one FIXED shell script (NODE_SCRIPT, built once at startup from config)
# run over `ssh -o BatchMode=yes` — no request can trigger, schedule or
# parameterise it. A peer that does not answer within PEER_TIMEOUT_S reads
# "unreachable"; the page never errors over it.

ENGINE_LABEL = {"llama": "llama.cpp", "vllm": "vLLM", "tensorfold": "TensorFold"}
# The Spark pair's inference units (systemd --user), in the order shown.
INFERENCE_UNITS = ("deepseek-v41-inference", "hy4-vllm-inference", "spark-hydra-inference")
SAMPLE_S = 5.0            # node samplers (head and peers)
SERVE_SAMPLE_S = 3.0      # engine / gate counters (tok/s, req/min)
PEER_TIMEOUT_S = 6.0      # one peer run, whole (ssh connect is capped at 3 s)
HISTORY_S = 15 * 60       # sparklines: the last 15 minutes
ACTIVE_DEVICE_S = 15 * 60 # a device seen by the gate within this is "active"
_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")
_PATH_RE = re.compile(r"^~?[A-Za-z0-9._/-]+$")


def _kv_file(path):
    """KEY=value pairs of a shell-style config file (last one wins), quotes
    stripped; never sourced, never executed. {} when unreadable."""
    out = {}
    try:
        with open(path) as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, _, v = ln.partition("=")
                k = k.strip()
                if k.startswith("export "):
                    k = k[7:].strip()
                v = v.split(" #", 1)[0].strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                if re.match(r"^[A-Z_][A-Z0-9_]*$", k):
                    out[k] = v
    except OSError:
        pass
    return out


def _setting(name, conf=None):
    """A dashboard setting: env OPENBEAST_<name>, env <name>, then the conf
    key <name> in openbeast.conf. None when none of them sets it."""
    for k in (f"OPENBEAST_{name}", name):
        v = os.environ.get(k)
        if v is not None and v.strip():
            return v.strip()
    conf = _kv_file(os.path.join(REPO_DIR, "openbeast.conf")) if conf is None else conf
    v = conf.get(name, "").strip()
    return v or None


def _ssh_aliases(path=None):
    """{hostname: alias} from ~/.ssh/config, so a cluster.env node written as
    max@10.100.216.2 is shown by the name the operator uses (spark-2)."""
    path = path or os.path.expanduser("~/.ssh/config")
    out, hosts = {}, []
    try:
        with open(path) as fh:
            for ln in fh:
                parts = ln.strip().split(None, 1)
                if len(parts) != 2 or parts[0].startswith("#"):
                    continue
                key, val = parts[0].lower(), parts[1].strip()
                if key == "host":
                    hosts = [h for h in val.split() if not any(c in h for c in "*?!")]
                elif key == "hostname" and hosts:
                    out.setdefault(val, hosts[0])
    except OSError:
        pass
    return out


def discover_peers(repo=None, conf=None, aliases=None):
    """The peer nodes to show beside this one: [(ssh target, label, rank)].

    DASHBOARD_PEERS (env or openbeast.conf), comma/space separated ssh
    targets, wins; `none` (or `off`) shows this node alone. Unset: the pair's
    own config, scripts/backends/cluster.env CLUSTER_NODES (rank order, the
    `local` entry being this node), which exists only on a Spark head — so a
    single-node rig shows itself and nothing else.
    """
    repo = REPO_DIR if repo is None else repo
    raw = _setting("DASHBOARD_PEERS", conf)
    aliases = _ssh_aliases() if aliases is None else aliases
    if raw is not None:
        if raw.lower() in ("none", "off", "false", "0"):
            return []
        targets = [(t, None) for t in re.split(r"[,\s]+", raw) if t]
    else:
        nodes = _kv_file(os.path.join(repo, "scripts", "backends", "cluster.env")).get("CLUSTER_NODES", "")
        targets = [(t, i) for i, t in enumerate(nodes.split()) if t != "local"]
    out, seen = [], set()
    for t, rank in targets:
        if not _TARGET_RE.match(t) or t in seen:
            continue                 # never an ssh option, never a duplicate
        seen.add(t)
        host = t.rsplit("@", 1)[-1]
        out.append((t, aliases.get(host, host), rank))
    return out


def _head_name():
    """This node's label: its tailnet name's first label (spark-1), else the
    hostname."""
    for p in (os.path.join(REPO_DIR, ".run", "webui-tailnet-name"),):
        try:
            v = open(p).read().strip().split(".", 1)[0]
            if re.match(r"^[A-Za-z0-9-]{1,63}$", v):
                return v
        except OSError:
            pass
    return (os.environ.get("OPENBEAST_TS_HOSTNAME") or socket.gethostname().split(".", 1)[0]
            or "this node")


def _disk_path(conf=None):
    """The model volume: DASHBOARD_DISK_PATH, WEIGHTS_DIR (the rig's
    weights), the Spark's MODELS_DIR (spark.env), else the checkout."""
    conf = _kv_file(os.path.join(REPO_DIR, "openbeast.conf")) if conf is None else conf
    spark = _kv_file(os.path.join(REPO_DIR, "scripts", "backends", "spark.env"))
    for v in (_setting("DASHBOARD_DISK_PATH", conf), conf.get("WEIGHTS_DIR"),
              spark.get("MODELS_DIR")):
        if v and _PATH_RE.match(v) and ".." not in v:
            return v
    w = os.path.join(REPO_DIR, "weights")
    return w if os.path.isdir(w) else os.path.abspath(REPO_DIR)


# The GPU query. power.draw is the driver's ~1 s average (board power on a
# discrete card, the GPU rail only on GB10); .instant and .average are asked
# for separately where the driver knows them (R530+), and an older driver
# that rejects the long query gets the short one. enforced.power.limit is
# [N/A] on GB10.
_SMI_LONG = ("name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw,"
             "power.draw.instant,power.draw.average,enforced.power.limit")
_SMI_SHORT = "name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw"

# NVML's cumulative energy counter (mJ since the driver loaded; Volta and
# later, no root), summed over every GPU, with this host's CLOCK_MONOTONIC.
# Two readings give the EXACT mean power between them, which one 1 s
# power.draw sample every 5 s cannot (under bursty load it is off by half;
# review 2026-11-22). Anything failing prints nothing: the rest of the probe
# stands. No double quotes would survive the sh single quotes, so none.
_NVML_PY = "\n".join((
    "import ctypes as c,time",
    "try:",
    " n=c.CDLL('libnvidia-ml.so.1')",
    " if n.nvmlInit_v2()==0:",
    "  k=c.c_uint();t=0;ok=n.nvmlDeviceGetCount_v2(c.byref(k))==0 and k.value>0",
    "  for i in range(k.value if ok else 0):",
    "   h=c.c_void_p();e=c.c_ulonglong()",
    "   if n.nvmlDeviceGetHandleByIndex_v2(i,c.byref(h)) or n.nvmlDeviceGetTotalEnergyConsumption(h,c.byref(e)):",
    "    ok=False;break",
    "   t+=e.value",
    "  if ok:print('nrg|%d|%d|%.3f'%(t,k.value,time.monotonic()))",
    "  n.nvmlShutdown()",
    "except Exception:pass",
)).replace("'", '"')


def node_script(disk_path):
    """The one fixed, read-only probe a node runs (locally, or on a peer over
    ssh as `sh -s` on stdin). Each line is `kind|fields`; a missing tool or
    file is just a missing line. disk_path comes from config at startup and
    is charset-checked; it is never a request value.

    nvidia-smi and the NVML read each run under coreutils `timeout -k`, well
    inside PEER_TIMEOUT_S: a wedged driver costs the GPU lines, not the whole
    node, and the hung process is killed instead of orphaned (review
    2026-11-22: one leaked per sample). Power rails beyond the GPU are every
    readable hwmon power*_input / power*_average, by name and label (hwmon
    numbers are not stable); parse_node decides what each one is."""
    if not _PATH_RE.match(disk_path) or ".." in disk_path:
        disk_path = "~"
    d = '"$HOME' + disk_path[1:] + '"' if disk_path.startswith("~") else '"' + disk_path + '"'
    smi = "nvidia-smi --query-gpu=%s --format=csv,noheader,nounits 2>/dev/null"
    return "\n".join((
        "export LC_ALL=C",
        f"D={d}; [ -d \"$D\" ] || D=\"$HOME\"",
        "T=; T2=; if command -v timeout >/dev/null 2>&1; then T='timeout -k 1 4'; T2='timeout -k 1 2'; fi",
        "echo \"host|$(hostname 2>/dev/null)\"",
        "if command -v nvidia-smi >/dev/null 2>&1; then",
        f"  o=$($T {smi % _SMI_LONG}); rc=$?",
        "  if [ -z \"$o\" ] && [ $rc -ne 124 ] && [ $rc -ne 137 ]; then",
        f"    o=$($T {smi % _SMI_SHORT}); rc=$?",
        "  fi",
        "  if [ -n \"$o\" ]; then printf '%s\\n' \"$o\" | sed 's/^/gpu|/'; else echo \"gpuerr|$rc\"; fi",
        "  if [ -n \"$o\" ] && command -v python3 >/dev/null 2>&1; then",
        "    $T2 python3 -I -c '" + _NVML_PY + "' 2>/dev/null",
        "  fi",
        "fi",
        "for h in /sys/class/hwmon/hwmon*; do [ -r \"$h/name\" ] || continue; nm=$(cat \"$h/name\" 2>/dev/null)",
        "  for f in \"$h\"/power*_input \"$h\"/power*_average; do [ -r \"$f\" ] || continue",
        "    b=${f%_*}; v=$(cat \"$f\" 2>/dev/null) || continue; cap=; [ -e \"${b}_cap\" ] && cap=cap",
        "    echo \"pwr|$nm|${b##*/}|$(cat \"${b}_label\" 2>/dev/null)|${f##*_}|$v|$cap\"",
        "  done",
        "done",
        "[ -r /proc/loadavg ] && echo \"load|$(cat /proc/loadavg)\"",
        "echo \"ncpu|$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null)\"",
        "[ -r /proc/stat ] && head -1 /proc/stat | sed 's/^/stat|/'",
        "[ -r /proc/meminfo ] && grep -E '^(MemTotal|MemAvailable):' /proc/meminfo | sed 's/^/mem|/'",
        "[ -r /proc/uptime ] && echo \"uptime|$(cut -d' ' -f1 /proc/uptime)\"",
        "df -P -k \"$D\" 2>/dev/null | tail -1 | sed 's/^/disk|/'",
        "for p in /sys/class/infiniband/*/ports/*; do [ -r \"$p/state\" ] || continue",
        "  h=${p%/ports/*}; h=${h##*/}; n=${p##*/}",
        "  nd=$(ls \"/sys/class/infiniband/$h/device/net\" 2>/dev/null | head -1)",
        "  mtu=$(cat \"/sys/class/net/$nd/mtu\" 2>/dev/null)",
        "  echo \"ib|$h|$n|$(cat \"$p/state\")|$(cat \"$p/counters/port_xmit_data\" 2>/dev/null)|"
        "$(cat \"$p/counters/port_rcv_data\" 2>/dev/null)|$nd|$mtu|$(cat \"$p/rate\" 2>/dev/null)\"",
        "done",
        "exit 0",
        ""))


def _num(v, cast=float):
    """v as a finite number, else None: NaN or Infinity never reach a view
    (json.dumps would write them, and the page's JSON.parse would choke)."""
    try:
        f = float(v)
        if not math.isfinite(f):
            return None
        return cast(f)
    except (TypeError, ValueError, OverflowError):
        return None


_RAIL_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _rail(fields):
    """One `pwr|name|channel|label|input-or-average|microwatts|cap` probe line
    -> {key, label, kind, w, note}, or None.

    What each hwmon power channel IS, never guessed beyond the kernel's own
    name and label:
    - power_meter (ACPI000D): whole-system power -> kind "system".
    - amdgpu "PPT" with no power cap: an AMD APU's integrated graphics
      reporting the SoC's socket power (Package Power Tracking) -> kind
      "cpu", "CPU package (PPT)". The APU SMU reports whole watts and the
      driver files them in the milliwatt field (the RTX rig's Ryzen 9
      9950X3D idles at "24-59 mW"; physically 24-59 W), so a reading under
      256 "mW" in whole mW steps is read as watts and says so in `note`.
    - amdgpu with a cap: a discrete AMD card's board power -> kind "gpu".
    - anything else: a named rail -> kind "rail".
    """
    if len(fields) < 5:
        return None
    name = _RAIL_NAME_RE.sub("", fields[0])[:24]
    ch = _RAIL_NAME_RE.sub("", fields[1])[:16]
    label = re.sub(r"[^\w .+-]", "", fields[2])[:32].strip()
    uw = _num(fields[4])
    has_cap = len(fields) > 5 and fields[5].strip() == "cap"
    if not name or uw is None or uw < 0:
        return None
    w, note = uw / 1e6, None
    if name == "power_meter":
        kind, text = "system", "system (ACPI power meter)"
    elif name == "amdgpu" and not has_cap and label.upper() == "PPT":
        kind, text = "cpu", "CPU package (PPT)"
        if 0 < uw < 256000 and uw % 1000 == 0:
            w, note = uw / 1000, "amdgpu APU: whole watts reported in the mW field, read as W"
    elif name == "amdgpu":
        kind, text = "gpu", "AMD GPU board (amdgpu)"
    else:
        kind, text = "rail", f"{name} {label or ch}".strip()
    if w > 5000:
        return None                          # not a reading
    return {"key": f"{name}/{ch}", "label": text, "kind": kind, "w": round(w, 2), "note": note}


def parse_node(out):
    """NODE_SCRIPT's output -> raw readings (no rates yet)."""
    raw = {"gpus": [], "ib": []}
    for ln in out.splitlines():
        kind, _, rest = ln.partition("|")
        if kind == "host":
            raw["hostname"] = rest.strip()[:64] or None
        elif kind == "gpu":
            p = [x.strip() for x in rest.split(",")]
            if len(p) >= 5 and p[0]:
                opt = lambda i: _num(p[i]) if len(p) > i else None
                raw["gpus"].append({"name": p[0][:80], "used": _smi_int(p[1]), "total": _smi_int(p[2]),
                                    "util": _smi_int(p[3]), "temp": _smi_int(p[4]),
                                    "power": opt(5), "power_inst": opt(6), "power_avg": opt(7),
                                    "limit": opt(8)})
        elif kind == "gpuerr":
            raw["gpu_error"] = _num(rest, int)
        elif kind == "nrg":
            p = rest.split("|")
            if len(p) >= 3:
                e, n, t = _num(p[0], int), _num(p[1], int), _num(p[2])
                if e is not None and e >= 0 and t is not None and t > 0:
                    raw["energy"] = {"mj": e, "gpus": n, "mono": t}
        elif kind == "pwr":
            r = _rail(rest.split("|"))
            rails = raw.setdefault("rails", {})
            # one reading per channel (power1_input beats power1_average)
            if r and r["key"] not in rails and len(rails) < 8:
                rails[r["key"]] = r
        elif kind == "load":
            p = rest.split()
            if len(p) >= 3:
                raw["load"] = [_num(x) for x in p[:3]]
        elif kind == "ncpu":
            raw["ncpu"] = _num(rest, int)
        elif kind == "stat":
            p = rest.split()
            if len(p) > 4 and p[0] == "cpu":
                vals = [_num(x, int) or 0 for x in p[1:]]
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                raw["cpu"] = (sum(vals), idle)
        elif kind == "mem":
            k, _, v = rest.partition(":")
            n = _num(v.split()[0] if v.split() else None, int)
            if n is not None:
                raw[k.strip()] = n // 1024          # MiB
        elif kind == "uptime":
            raw["uptime"] = _num(rest)
        elif kind == "disk":
            p = rest.split()
            if len(p) >= 4:
                tot, used, avail = (_num(x, int) for x in p[1:4])
                if tot:
                    raw["disk"] = (tot * 1024, (used or 0) * 1024, (avail or 0) * 1024)
        elif kind == "ib":
            p = rest.split("|")
            if len(p) >= 8:
                state = p[2].split(":", 1)[-1].strip()
                raw["ib"].append({"hca": p[0][:32], "port": p[1][:4], "state": state[:16],
                                  "tx": _num(p[3], int), "rx": _num(p[4], int),
                                  "netdev": p[5][:32] or None, "mtu": _num(p[6], int),
                                  "rate": p[7].strip()[:40] or None})
    return raw


def node_view(raw, prev=None, dt=None):
    """Raw readings (+ the previous sample, for rates) -> the node object
    /api/cluster serves. Any reading missing is null, never a guess."""
    gpus = raw.get("gpus") or []
    ram_t, ram_a = raw.get("MemTotal"), raw.get("MemAvailable")
    ram = None
    if ram_t and ram_a is not None:
        ram = {"used_mib": ram_t - ram_a, "total_mib": ram_t,
               "pct": round(100 * (ram_t - ram_a) / ram_t, 1)}
    gpu = None
    if gpus:
        names = [g["name"] for g in gpus]
        name = names[0] if len(set(names)) == 1 else " + ".join(names)

        def fold(key, how):
            vals = [g[key] for g in gpus]
            return None if any(v is None for v in vals) else how(vals)
        unified = all(g["used"] is None and g["total"] is None for g in gpus)
        if unified:
            used, total = (ram["used_mib"], ram["total_mib"]) if ram else (None, None)
        else:
            used, total = fold("used", sum), fold("total", sum)
        gpu = {"name": name, "count": len(gpus), "unified": unified,
               "mem_used_mib": used, "mem_total_mib": total,
               "mem_pct": round(100 * used / total, 1) if used is not None and total else None,
               "util_pct": fold("util", max), "temp_c": fold("temp", max),
               "power_w": _r1(fold("power", sum)),
               "power_instant_w": _r1(fold("power_inst", sum)),
               "power_avg_w": _r1(fold("power_avg", sum)),
               "power_limit_w": _r1(fold("limit", sum))}
    cpu = {"load": raw.get("load"), "ncpu": raw.get("ncpu"), "util_pct": None}
    if prev and raw.get("cpu") and prev.get("cpu"):
        dtot = raw["cpu"][0] - prev["cpu"][0]
        didle = raw["cpu"][1] - prev["cpu"][1]
        if dtot > 0 and 0 <= didle <= dtot:
            cpu["util_pct"] = round(100 * (1 - didle / dtot), 1)
    disk = None
    if raw.get("disk"):
        tot, used, avail = raw["disk"]
        # df's Use%: used / (used + available), so the root-reserved blocks
        # count as unavailable, and the % agrees with the free figure beside it.
        disk = {"total_b": tot, "used_b": used, "free_b": avail,
                "pct": round(100 * used / (used + avail), 1) if used + avail > 0 else None}
    prev_ib = {(p["hca"], p["port"]): p for p in (prev or {}).get("ib", [])}
    fabric = []
    for p in raw.get("ib", []):
        row = {k: p[k] for k in ("hca", "port", "state", "netdev", "mtu", "rate")}
        row["tx_bps"] = row["rx_bps"] = None
        q = prev_ib.get((p["hca"], p["port"]))
        if q and dt and dt > 0:
            for k in ("tx", "rx"):
                if p[k] is not None and q[k] is not None and p[k] >= q[k]:
                    # port_*_data counts 4-octet words (IB spec), so x4 for bytes.
                    row[f"{k}_bps"] = round((p[k] - q[k]) * 4 * 8 / dt)
        fabric.append(row)
    out = {"hostname": raw.get("hostname"), "gpu": gpu, "cpu": cpu, "ram": ram,
           "disk": disk, "uptime_s": raw.get("uptime"), "fabric": fabric,
           "power": power_now(gpu, raw.get("rails") or {})}
    if gpu is None and raw.get("gpu_error") is not None:
        out["gpu_error"] = raw["gpu_error"]
    return out


def _r1(v):
    return None if v is None else round(v, 1)


# What the node's power figure covers, in words the page shows as a footnote.
_SCOPE_GB10 = ("GPU rail only: GB10 exposes no module or system power (nvidia-smi module "
               "power reads N/A; no hwmon, RAPL or powercap power sensor). The whole box "
               "draws more; wall power needs an external meter.")
_SCOPE_DISCRETE = "board power of the whole card (GPU + VRAM), not the rest of the machine"


def power_now(gpu, rails):
    """The node's power reading NOW, labelled by what is actually measured.

    gpu_w is the instantaneous GPU reading (power.draw.instant) where the
    driver has one, else power.draw (its ~1 s average); gpu_avg_w is that
    1 s average. A system-power sensor (ACPI power meter), when one exists,
    IS the node figure and the rails are parts of it; otherwise total_w sums
    the GPU and every other readable rail and the label says which ("GPU +
    CPU package"). Never called system power without a system sensor.
    """
    g = gpu or {}
    gpu_w = g.get("power_instant_w")
    if gpu_w is None:
        gpu_w = g.get("power_w")
    rl = [dict(r) for r in rails.values()]
    system = [r for r in rl if r["kind"] == "system"]
    parts = []
    if gpu_w is not None:
        parts.append({"key": "gpu", "label": "GPU", "kind": "gpu-nvidia", "w": gpu_w, "note": None})
    parts += [r for r in rl if r["kind"] != "system"]
    if system:
        total, label, measured = system[0]["w"], "system power", "system"
    elif parts:
        total = round(sum(r["w"] for r in parts), 1)
        if len(parts) == 1 and gpu_w is not None:
            label, measured = "GPU power", "gpu"
        else:
            names = {"gpu-nvidia": "GPU", "cpu": "CPU package", "gpu": "AMD GPU"}
            label = " + ".join(names.get(r["kind"], r["label"]) for r in parts)
            measured = "partial"
    else:
        return None
    gb10 = "GB10" in (g.get("name") or "")
    scope = _SCOPE_GB10 if gb10 else _SCOPE_DISCRETE if gpu_w is not None else None
    return {"total_w": total, "label": label, "measured": measured,
            "gpu_w": gpu_w, "gpu_avg_w": g.get("power_avg_w"),
            "gpu_source": "power.draw.instant" if g.get("power_instant_w") is not None else "power.draw",
            "gpu_limit_w": g.get("power_limit_w"), "gpu_scope": scope,
            "rails": parts + system}


_events = collections.deque(maxlen=40)
_events_lock = threading.Lock()


def event(text, level="info"):
    with _events_lock:
        _events.appendleft({"t": round(time.time()), "level": level, "text": text[:160]})


def events():
    with _events_lock:
        return list(_events)


class _Ring:
    """Timestamped samples for the last HISTORY_S seconds. Capped by count
    as well as age, so a wall clock stepped backwards cannot grow it."""

    def __init__(self, span=HISTORY_S, interval=1.0):
        self.span, self._lock = span, threading.Lock()
        self._d = collections.deque(maxlen=int(span / max(0.5, interval)) + 32)

    def add(self, sample, now=None):
        now = time.time() if now is None else now
        with self._lock:
            self._d.append((now, sample))
            while self._d and now - self._d[0][0] > self.span:
                self._d.popleft()

    def set_last(self, key, value):
        """Set one field of the newest sample, under the lock."""
        with self._lock:
            if self._d:
                self._d[-1][1][key] = value

    def series(self, key, digits=1):
        with self._lock:
            return [None if s.get(key) is None else round(s[key], digits) for _, s in self._d]

    def times(self):
        """Each sample's wall time (epoch seconds), aligned with series()."""
        with self._lock:
            return [round(t, 1) for t, _ in self._d]

    def items(self):
        with self._lock:
            return list(self._d)


# ───────────────────────── power: history, windows, trend ─────────────────────────
#
# Every node sample becomes one INTERVAL [previous sample, this sample] with a
# mean power: for the GPU the NVML energy counter's delta over the node's own
# monotonic clock (exact, whatever the load did between samples), else the
# trapezoid of the two power.draw readings; for any other rail the trapezoid.
# Intervals are split exactly into wall-clock-aligned slots of two tiers:
# 5 s slots for the last hour, 1 min slots for the last 24 h. A slot holds
# the seconds it covers, the energy in them, and the lowest and highest
# interval mean seen in it. Averages are energy / covered seconds: time-
# weighted, and a gap (peer unreachable, dashboard down, a sample more than
# POWER_GAP_S late) is simply not covered, shown as coverage < 100 %, never
# as zeros. Persisted to .run/dashboard-power.json (temp file + rename).

POWER_FINE_S, POWER_FINE_SPAN = 5, 3600 + 120
POWER_COARSE_S, POWER_COARSE_SPAN = 60, 86400 + 300
POWER_GAP_S = max(3 * SAMPLE_S, 20.0)
POWER_WINDOWS = (("1m", 60), ("5m", 300), ("15m", 900), ("1h", 3600), ("24h", 86400))
# A window average needs this much of the window covered; below it the
# average reads null ("collecting") rather than a figure from a sliver.
POWER_MIN_COVERAGE = 0.1
CHART_WINDOWS = {"15m": (900, "fine"), "1h": (3600, "fine"), "6h": (21600, "coarse"),
                 "24h": (86400, "coarse")}
TREND_DEADBAND = 0.05      # within ±5 % of the baseline reads "steady"
POWER_FILE = "dashboard-power.json"
POWER_FILE_MAX = 4 << 20   # bytes; a bigger file is not read, not written
POWER_SAVE_S = 30.0
_COV, _E, _COVG, _EG, _MAX, _MIN = range(6)


class _Tier:
    """Wall-clock-aligned power slots: {slot start: [covered s, energy J,
    GPU-covered s, GPU energy J, max W, min W]}."""

    def __init__(self, step, span):
        self.step, self.span, self.slots = step, span, {}

    def add(self, a, b, p, pg):
        t = a
        while t < b - 1e-9:
            t0 = math.floor(t / self.step + 1e-9) * self.step
            end = min(b, t0 + self.step)
            dur = end - t
            s = self.slots.get(t0)
            if s is None:
                s = self.slots[t0] = [0.0, 0.0, 0.0, 0.0, p, p]
            s[_COV] += dur
            s[_E] += p * dur
            if pg is not None:
                s[_COVG] += dur
                s[_EG] += pg * dur
            s[_MAX], s[_MIN] = max(s[_MAX], p), min(s[_MIN], p)
            t = end

    def prune(self, now):
        lo, hi = now - self.span, now + 300
        for k in [k for k in self.slots if k < lo or k > hi]:
            del self.slots[k]
        cap = int(self.span / self.step) + 8
        if len(self.slots) > cap:
            for k in sorted(self.slots)[:len(self.slots) - cap]:
                del self.slots[k]

    def window(self, now, w):
        """(slot start, slot) for slots inside [now - w, now] by midpoint."""
        lo = now - w
        return sorted((k, v) for k, v in self.slots.items()
                      if k + self.step / 2 >= lo and k <= now)

    def dump(self):
        return [[int(k), round(v[_COV], 2), round(v[_E], 1), round(v[_COVG], 2), round(v[_EG], 1),
                 round(v[_MAX], 1), round(v[_MIN], 1)] for k, v in sorted(self.slots.items())]

    def load(self, rows, now):
        for r in rows if isinstance(rows, list) else []:
            if not (isinstance(r, list) and len(r) == 7):
                continue
            vals = [_num(x) for x in r]
            if any(v is None for v in vals):
                continue
            k, cov, e, covg, eg, mx, mn = vals
            if k % self.step or not (0 <= cov <= self.step + 1e-6) or not (0 <= covg <= cov + 1e-6) \
                    or e < 0 or eg < 0 or mn > mx:
                continue
            self.slots[int(k)] = [cov, e, covg, eg, mx, mn]
        self.prune(now)


class NodePower:
    """One node's power history: the interval maker and the two tiers."""

    def __init__(self, gap=POWER_GAP_S):
        self.gap = gap
        self.fine = _Tier(POWER_FINE_S, POWER_FINE_SPAN)
        self.coarse = _Tier(POWER_COARSE_S, POWER_COARSE_SPAN)
        self._prev = None          # (local monotonic, energy dict | None, gpu W | None, {rail: W})
        self._lock = threading.Lock()
        self.source = None         # "nvml-energy" | "power.draw" for the last interval

    def ingest(self, wall, mono, raw, view):
        """Turn this sample (and the previous one) into an interval."""
        g = (view or {}).get("gpu") or {}
        pw = (view or {}).get("power") or {}
        rails = {r["key"]: r["w"] for r in pw.get("rails", []) if r["key"] != "gpu"}
        system = any(r["kind"] == "system" for r in pw.get("rails", []))
        cur = (mono, (raw or {}).get("energy"), g.get("power_w"), rails, system)
        with self._lock:
            prev, self._prev = self._prev, cur
            if prev is None:
                return None
            dt = mono - prev[0]
            pg, src = None, None
            e0, e1 = prev[1], cur[1]
            if e0 and e1 and e0.get("gpus") == e1.get("gpus"):
                de, dtr = e1["mj"] - e0["mj"], e1["mono"] - e0["mono"]
                # The node's own clock, if it agrees with ours: a remote
                # reboot or driver reload (counter or clock going back) and
                # a wild clock fall back to the trapezoid.
                if de >= 0 and dtr > 0 and abs(dtr - dt) <= max(2.0, 0.5 * dt):
                    pg, dt, src = de / 1000.0 / dtr, dtr, "nvml-energy"
            if pg is None and prev[2] is not None and cur[2] is not None:
                pg, src = (prev[2] + cur[2]) / 2, "power.draw"
            if dt <= 0 or dt > self.gap:
                return None                      # a gap: not covered
            others = [(prev[3][k] + rails[k]) / 2 for k in rails if k in prev[3]]
            if system:
                sk = [k for k in rails if k in prev[3] and k.startswith("power_meter/")]
                if not sk:
                    return None
                p = (prev[3][sk[0]] + rails[sk[0]]) / 2
            elif pg is None and not others:
                return None
            else:
                p = (pg or 0.0) + sum(others)
            self.source = src
            for tier in (self.fine, self.coarse):
                tier.add(wall - dt, wall, p, pg)
                tier.prune(wall)
            return p

    def stats(self, now, w):
        """Window stats: time-weighted average, min and max of the ~5 s
        interval means, measured energy, coverage. Also the GPU-only
        average."""
        tier = self.fine if w <= 3600 else self.coarse
        with self._lock:
            rows = tier.window(now, w)
        cov = sum(v[_COV] for _, v in rows)
        e = sum(v[_E] for _, v in rows)
        covg = sum(v[_COVG] for _, v in rows)
        eg = sum(v[_EG] for _, v in rows)
        coverage = min(1.0, cov / w) if w else 0.0
        ok = coverage >= POWER_MIN_COVERAGE and cov > 0
        live = [v for _, v in rows if v[_COV] > 0]
        return {"avg_w": round(e / cov, 1) if ok else None,
                "gpu_avg_w": round(eg / covg, 1) if ok and covg > 0 else None,
                "min_w": round(min(v[_MIN] for v in live), 1) if ok and live else None,
                "max_w": round(max(v[_MAX] for v in live), 1) if ok and live else None,
                "energy_wh": round(e / 3600, 3), "covered_s": round(cov, 1),
                "coverage": round(coverage, 3)}

    def slot_means(self, tier_name, now, w):
        """{slot start: (mean W, covered fraction)} over a window."""
        tier = self.fine if tier_name == "fine" else self.coarse
        with self._lock:
            rows = tier.window(now, w)
        return {k: (v[_E] / v[_COV], v[_COV] / tier.step) for k, v in rows if v[_COV] > 0}

    def dump(self):
        with self._lock:
            return {"fine": self.fine.dump(), "coarse": self.coarse.dump()}

    def load(self, doc, now):
        if not isinstance(doc, dict):
            return
        with self._lock:
            self.fine.load(doc.get("fine"), now)
            self.coarse.load(doc.get("coarse"), now)


def trend(stats):
    """5 min average against the 15 min one (or the hour's, when 15 min is
    not there yet): rising / falling / steady within TREND_DEADBAND."""
    a = (stats.get("5m") or {}).get("avg_w")
    for base in ("15m", "1h"):
        b = (stats.get(base) or {}).get("avg_w")
        if a is not None and b:
            pct = (a - b) / b
            d = "rising" if pct > TREND_DEADBAND else "falling" if pct < -TREND_DEADBAND else "steady"
            return {"dir": d, "pct": round(100 * pct, 1), "vs": base, "now_avg_w": a, "base_avg_w": b}
    return {"dir": None, "pct": None, "vs": None, "now_avg_w": a, "base_avg_w": None}


def _kwh_price(conf=None):
    """DASHBOARD_KWH_PRICE (env or openbeast.conf), a positive number, else
    None; DASHBOARD_CURRENCY is its symbol or code (default "$")."""
    v = _num(_setting("DASHBOARD_KWH_PRICE", conf))
    if v is None or v <= 0 or v > 100:
        return None, None
    cur = _setting("DASHBOARD_CURRENCY", conf) or "$"
    cur = re.sub(r"[^\w$€£¥₹ .-]", "", cur)[:6] or "$"
    return v, cur


def power_summary(nodes, now=None, price=None, currency=None):
    """The power block /api/cluster serves: per node now / windows / trend,
    the cluster total, and the optional cost. `nodes` is [(name, state,
    view, NodePower)]."""
    now = time.time() if now is None else now
    out_nodes, totals = [], []
    for name, state, view, np_ in nodes:
        pw = (view or {}).get("power") if state == "up" else None
        st = {k: np_.stats(now, w) for k, w in POWER_WINDOWS}
        out_nodes.append({"name": name, "state": state, "now": pw,
                          "last": (view or {}).get("power") if state != "up" else None,
                          "windows": st, "trend": trend(st), "source": np_.source})
        if pw:
            totals.append(pw["total_w"])
    m = len(out_nodes)
    cl_windows = {}
    for k, w in POWER_WINDOWS:
        per = [n["windows"][k] for n in out_nodes]
        avgs = [p["avg_w"] for p in per]
        tier = "fine" if w <= 3600 else "coarse"
        step = POWER_FINE_S if tier == "fine" else POWER_COARSE_S
        means = [np_.slot_means(tier, now, w) for _, _, _, np_ in nodes]
        # The cluster's peak and low are of slots where EVERY node was
        # measured (a sum over a missing node is not the cluster).
        common = set.intersection(*(set(k2 for k2, (_, f) in mm.items() if f >= 0.5) for mm in means)) \
            if means else set()
        sums = [sum(mm[t][0] for mm in means) for t in common]
        full = all(a is not None for a in avgs) and m > 0
        cl_windows[k] = {
            "avg_w": round(sum(avgs), 1) if full else None,
            "min_w": round(min(sums), 1) if full and sums else None,
            "max_w": round(max(sums), 1) if full and sums else None,
            "energy_wh": round(sum(p["energy_wh"] for p in per), 3),
            "coverage": round(min((p["coverage"] for p in per), default=0.0), 3),
            "peak_resolution_s": step}
    labels = {n["now"]["label"] if n["now"] else (n["last"] or {}).get("label") for n in out_nodes}
    labels.discard(None)
    cluster = {"now_w": round(sum(totals), 1) if totals else None,
               "nodes_measured": len(totals), "nodes": m,
               "label": labels.pop() if len(labels) == 1 else "measured power" if labels else None,
               "windows": cl_windows, "trend": trend(cl_windows)}
    cost = None
    if price:
        basis, day_avg = next(((k, cl_windows[k]["avg_w"]) for k in ("24h", "1h", "15m")
                               if cl_windows[k]["avg_w"] is not None), (None, None))
        e24 = cl_windows["24h"]["energy_wh"] / 1000
        cost = {"price_per_kwh": price, "currency": currency,
                "per_day": round(day_avg * 24 / 1000 * price, 2) if day_avg is not None else None,
                "per_month": round(day_avg * 24 * 30.44 / 1000 * price, 2) if day_avg is not None else None,
                "basis_avg_w": day_avg, "basis_window": basis,
                "window_24h": round(e24 * price, 2), "window_24h_kwh": round(e24, 3),
                "window_24h_coverage": cl_windows["24h"]["coverage"],
                "note": "measured rails only (see each node's label), not wall power"}
    return {"generated_at": round(now), "nodes": out_nodes, "cluster": cluster, "cost": cost,
            "windows": [k for k, _ in POWER_WINDOWS], "sample_s": SAMPLE_S,
            "slot_s": {"fine": POWER_FINE_S, "coarse": POWER_COARSE_S},
            "min_coverage": POWER_MIN_COVERAGE, "trend_deadband_pct": round(100 * TREND_DEADBAND, 1)}


def power_chart(nodes, window, now=None):
    """/api/power: one window's series on the slot grid. Per node the slot
    means (null where under 30 % covered), the cluster total where every
    node is measured, and the window's averages to mark."""
    now = time.time() if now is None else now
    w, tier = CHART_WINDOWS.get(window, CHART_WINDOWS["15m"])
    window = window if window in CHART_WINDOWS else "15m"
    step = POWER_FINE_S if tier == "fine" else POWER_COARSE_S
    end = math.floor(now / step) * step
    grid = [end - i * step for i in range(int(w / step) - 1, -1, -1)]
    series, totals = [], []
    for name, state, view, np_ in nodes:
        mm = np_.slot_means(tier, now, w + step)
        vals = [round(mm[t][0], 1) if t in mm and mm[t][1] >= 0.3 else None for t in grid]
        st = np_.stats(now, w)
        series.append({"name": name, "values": vals, "avg_w": st["avg_w"], "coverage": st["coverage"]})
    if len(series) > 1:
        for i in range(len(grid)):
            col = [s["values"][i] for s in series]
            totals.append(round(sum(col), 1) if all(v is not None for v in col) else None)
    avgs = [s["avg_w"] for s in series]
    return {"window": window, "window_s": w, "step_s": step, "t0": grid[0] if grid else None,
            "t_end": end, "now": round(now, 1), "nodes": series,
            "total": totals if len(series) > 1 else None,
            "total_avg_w": round(sum(avgs), 1) if len(series) > 1 and all(a is not None for a in avgs) else None}


def _power_path():
    run = os.path.join(REPO_DIR, ".run")
    return os.path.join(run, POWER_FILE) if os.path.isdir(run) else None


def save_power(samplers, path=None, now=None):
    """Write every node's tiers to .run/dashboard-power.json: a temp file in
    the same directory, fsynced, then renamed over the old one, so a crash
    mid-write leaves the previous file whole. A document over
    POWER_FILE_MAX drops the 5 s tier first, and is not written at all if
    still too big. Returns the bytes written (0: nothing)."""
    path = path or _power_path()
    if not path:
        return 0
    now = time.time() if now is None else now
    doc = {"v": 1, "saved_at": round(now), "fine_s": POWER_FINE_S, "coarse_s": POWER_COARSE_S,
           "nodes": {s.name: s.power.dump() for s in samplers}}
    body = json.dumps(doc, separators=(",", ":")).encode()
    if len(body) > POWER_FILE_MAX:
        for n in doc["nodes"].values():
            n["fine"] = []
        body = json.dumps(doc, separators=(",", ":")).encode()
        if len(body) > POWER_FILE_MAX:
            return 0
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        return 0
    return len(body)


def load_power(samplers, path=None, now=None):
    """Restore what save_power wrote, for the nodes configured now. A file
    that is missing, too big, corrupt or of another layout restores nothing
    (and is overwritten by the next save); slots past their tier's span are
    dropped. Returns how many nodes were restored."""
    path = path or _power_path()
    if not path:
        return 0
    now = time.time() if now is None else now
    try:
        if os.path.getsize(path) > POWER_FILE_MAX:
            return 0
        with open(path, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return 0
    if not (isinstance(doc, dict) and doc.get("v") == 1 and doc.get("fine_s") == POWER_FINE_S
            and doc.get("coarse_s") == POWER_COARSE_S and isinstance(doc.get("nodes"), dict)):
        return 0
    n = 0
    for s in samplers:
        if s.name in doc["nodes"]:
            s.power.load(doc["nodes"][s.name], now)
            n += 1
    return n


def _run_node(target):
    """(stdout, error) of NODE_SCRIPT on `target` (None: this node)."""
    cmd = (["sh", "-s"] if target is None else
           ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "-o", "ServerAliveInterval=2",
            "-o", "ServerAliveCountMax=1", "-o", "LogLevel=ERROR", "-T", "--", target, "sh -s"])
    # Its own process group, killed whole on a timeout: subprocess.run's
    # timeout killed only `sh -s`, and a hung nvidia-smi under it (a
    # grandchild, in a pipeline) was orphaned on every sample (review
    # 2026-11-22). The probe also bounds nvidia-smi itself (node_script).
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError as e:
        return None, "ssh not available" if target else f"probe failed ({e.__class__.__name__})"
    try:
        out, _ = p.communicate(NODE_SCRIPT, timeout=PEER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            os.killpg(p.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            p.communicate(timeout=2)
        return None, f"no answer within {PEER_TIMEOUT_S:.0f} s"
    if target is not None and p.returncode == 255:
        return None, "ssh failed (unreachable, or key login refused)"
    if not (out or "").strip():
        return None, f"empty answer (exit {p.returncode})"
    return out, None


class NodeSampler:
    """One node's metrics on a timer: the head runs NODE_SCRIPT locally, a
    peer over ssh. Only the background thread ever runs it."""

    def __init__(self, name, target=None, role="head", rank=None, interval=SAMPLE_S):
        self.name, self.target, self.role, self.rank = name, target, role, rank
        self.interval = interval
        self.history = _Ring(interval=interval)
        self.power = NodePower()
        self._lock = threading.Lock()
        self._prev = None           # (monotonic, raw)
        self.view, self.error, self.ok_at, self.state = None, None, None, "pending"

    def sample(self, run=_run_node, wall=None):
        out, err = run(self.target)
        now = time.monotonic()
        wall = time.time() if wall is None else wall
        if err is None:
            raw = parse_node(out)
            prev = self._prev
            view = node_view(raw, prev[1] if prev else None, now - prev[0] if prev else None)
            self._prev = (now, raw)
            g = view["gpu"] or {}
            self.power.ingest(wall, now, raw, view)
            self.history.add({"util": g.get("util_pct"), "mem": g.get("mem_pct"),
                              "power": g.get("power_w"), "cpu": view["cpu"]["util_pct"]}, wall)
        else:
            # A failed sample is a GAP on the time axis, not a skipped one:
            # the sparkline breaks there and its "x ago" stays true.
            self.history.add({"util": None, "mem": None, "power": None, "cpu": None}, wall)
        self._set_state(None if err is None else err, view if err is None else None)

    def _set_state(self, err, view=None):
        was = self.state
        with self._lock:
            if err is None:
                self.view, self.error, self.ok_at, self.state = view, None, time.time(), "up"
            else:
                self.error, self.state = err, "unreachable"
        if was != self.state and not (was == "pending" and self.state == "up"):
            event(f"{self.name} {'is back' if self.state == 'up' else 'unreachable: ' + err}",
                  "info" if self.state == "up" else "warn")

    def snapshot(self):
        with self._lock:
            view, err, ok_at, state = self.view, self.error, self.ok_at, self.state
        out = {"name": self.name, "role": self.role, "rank": self.rank, "state": state,
               "error": err, "last_ok_age_s": round(time.time() - ok_at, 1) if ok_at else None,
               "interval_s": self.interval}
        out.update(view or {"hostname": None, "gpu": None, "cpu": None, "ram": None,
                            "disk": None, "uptime_s": None, "fabric": [], "power": None})
        out["history"] = {k: self.history.series(k) for k in ("util", "mem", "power", "cpu")}
        out["history"]["t"] = self.history.times()
        return out

    def power_row(self):
        with self._lock:
            return self.name, self.state, self.view, self.power

    def loop(self):
        while True:
            t0 = time.monotonic()
            try:
                self.sample()
            except Exception as e:             # never let one bad parse end the loop
                self._set_state(f"probe error ({e.__class__.__name__})")
            time.sleep(max(1.0, self.interval - (time.monotonic() - t0)))


def _prom_sum(body, name):
    v = _prom(body, name)
    return sum(v) if v else None


def _prom_by(body, name, label):
    """{label value: summed sample} for one metric."""
    out = {}
    pat = re.compile(r'%s="((?:[^"\\]|\\.)*)"' % re.escape(label))
    for line in body.splitlines():
        if not line.startswith(name + "{"):
            continue
        m = pat.search(line)
        try:
            v = float(line.rsplit(" ", 1)[1])
        except (IndexError, ValueError):
            continue
        if not math.isfinite(v):
            continue
        k = m.group(1) if m else ""
        out[k] = out.get(k, 0) + v
    return out


def _gate_get(path):
    """A rig-local beast-gate introspection read: the same locality token
    doctor.sh presents (.run/edge-local.token, 0600, re-minted per gate
    start). Counts only ever leave this function — never a device name."""
    try:
        tok = open(os.path.join(REPO_DIR, ".run", "edge-local.token")).read().strip()
    except OSError:
        tok = ""
    port = os.environ.get("OPENBEAST_EDGE_PORT", "").strip() or "8090"
    if not port.isdigit():
        return None, ""
    try:
        req = urllib.request.Request(f"http://{H}:{port}{path}")
        if tok:
            req.add_unredirected_header("X-OpenBeast-Local", tok)
        with _OPENER.open(req, timeout=2) as r:
            return r.status, r.read(1 << 20).decode("utf-8", "replace")
    except Exception:
        return None, ""


def _active_devices(now=None):
    """How many devices beast-gate saw within ACTIVE_DEVICE_S (from
    .run/clients-lastseen.json; the names are read and dropped here)."""
    try:
        with open(os.path.join(REPO_DIR, ".run", "clients-lastseen.json")) as fh:
            seen = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(seen, dict):
        return None
    now = time.time() if now is None else now
    n = 0
    for ts in seen.values():
        try:
            # UTC ("Z", agents/edge.py): timegm, never mktime - time.timezone,
            # which was an hour off whenever the host's zone is on summer
            # time (the count read 0 from March to November; review 2026-11-22).
            t = calendar.timegm(time.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ"))
        except (ValueError, OverflowError):
            continue
        if now - t <= ACTIVE_DEVICE_S:
            n += 1
    return n


def engine_counters(backend, metrics_body, health_body=""):
    """(generated, prompt) token counters of the engine, either None."""
    if backend == "vllm":
        return (_prom_sum(metrics_body, "vllm:generation_tokens_total"),
                _prom_sum(metrics_body, "vllm:prompt_tokens_total"))
    if backend == "llama":
        return (_prom_sum(metrics_body, "llamacpp:tokens_predicted_total"),
                _prom_sum(metrics_body, "llamacpp:prompt_tokens_total"))
    gen = prm = None
    for n in ("tensorfold:generation_tokens_total", "tensorfold_generation_tokens_total",
              "vllm:generation_tokens_total"):
        gen = gen if gen is not None else _prom_sum(metrics_body, n)
    for n in ("tensorfold:prompt_tokens_total", "tensorfold_prompt_tokens_total",
              "vllm:prompt_tokens_total"):
        prm = prm if prm is not None else _prom_sum(metrics_body, n)
    try:
        h = json.loads(health_body) if health_body else {}
    except ValueError:
        h = {}
    if isinstance(h, dict):
        for k in ("generation_tokens_total", "tokens_generated_total", "completion_tokens_total",
                  "generated_tokens", "tokens_generated"):
            if gen is None and isinstance(h.get(k), (int, float)) and not isinstance(h.get(k), bool):
                gen = float(h[k])
        for k in ("prompt_tokens_total", "prompt_tokens"):
            if prm is None and isinstance(h.get(k), (int, float)) and not isinstance(h.get(k), bool):
                prm = float(h[k])
    return gen, prm


_IDENTITY_REASONS = ("no_key", "unknown_key", "revoked")


def _counter_window(v, base=10.0):
    """The rate window for a counter printed with 6 significant digits
    (llama.cpp's %g): 10 s, widened to twice the print step once the counter
    passes 1e6, at most 120 s (the ring keeps HISTORY_S)."""
    if v is None or v < 1e6:
        return base
    step = 10 ** (math.floor(math.log10(v)) - 5)
    return min(120.0, max(base, 2 * step))


def llama_inflight_tokens(status, body):
    """Tokens decoded so far by every busy llama.cpp slot (/slots), or None
    when /slots is not readable. Added to tokens_predicted_total (which
    moves only when a request finishes), the counter is live and continuous:
    at completion a request's tokens leave this sum and join the total in
    the same step."""
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    live = 0
    for s in data:
        if not isinstance(s, dict) or not (s.get("is_processing") or s.get("state", 0) != 0):
            continue
        nt = s.get("next_token")
        nt = nt[0] if isinstance(nt, list) and nt else nt
        n = (nt.get("n_decoded") if isinstance(nt, dict) else None) or s.get("n_decoded") or 0
        if isinstance(n, (int, float)) and not isinstance(n, bool) and n >= 0:
            live += int(n)
    return live


def _rate(ring, key, now, window=10.0):
    """Per-second rate of a counter over about `window` s of ring samples;
    None across a reset or with too little history."""
    items = ring.items()
    if not items or items[-1][1].get(key) is None:
        return None
    t1, s1 = items[-1]
    for t0, s0 in reversed(items[:-1]):
        if s0.get(key) is None:
            return None
        if t1 - t0 >= window or t0 == items[0][0]:
            if t1 - t0 <= 0 or s1[key] < s0[key]:
                return None
            return (s1[key] - s0[key]) / (t1 - t0)
    return None


def _systemctl_units(units=INFERENCE_UNITS):
    """{unit: (active_state, active_since_monotonic_s)} via one read-only
    `systemctl --user show`; {} without systemd."""
    try:
        r = subprocess.run(["systemctl", "--user", "show", "-p", "Id", "-p", "ActiveState",
                            "-p", "ActiveEnterTimestampMonotonic", *[u + ".service" for u in units]],
                           capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    out = {}
    for block in r.stdout.strip().split("\n\n"):
        kv = dict(ln.split("=", 1) for ln in block.splitlines() if "=" in ln)
        uid = kv.get("Id", "").removesuffix(".service")
        if uid:
            out[uid] = (kv.get("ActiveState", ""), _num(kv.get("ActiveEnterTimestampMonotonic"), int))
    return out


def _proc_uptime():
    try:
        return float(open("/proc/uptime").read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _pid_age(pidfile):
    """Seconds the recorded process has run, from /proc/<pid>/stat; None."""
    try:
        pid = int(open(pidfile).read().split()[0])
        stat = open(f"/proc/{pid}/stat").read()
        start = int(stat.rsplit(")", 1)[1].split()[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None
    up = _proc_uptime()
    return None if up is None else max(0.0, up - start)


def inference_unit(units=None):
    """{name, active, uptime_s} of what serves the model on this node."""
    if _BACKEND == "llama":
        age = _pid_age(os.path.join(REPO_DIR, ".run", "llama.pid"))
        return {"name": "llama-server" if age is not None else None, "active": age is not None,
                "uptime_s": round(age) if age is not None else None}
    units = _systemctl_units() if units is None else units
    up = _proc_uptime()
    for u in INFERENCE_UNITS:
        state, since = units.get(u, ("", None))
        if state == "active":
            age = up - since / 1e6 if up is not None and since else None
            return {"name": u, "active": True, "uptime_s": round(age) if age and age > 0 else None}
    return {"name": None, "active": False, "uptime_s": None}


class ServeSampler:
    """The head's serving and gate counters on a timer: token rates and
    request rates need two readings, and history needs a clock."""

    def __init__(self, interval=SERVE_SAMPLE_S):
        self.interval = interval
        self.ring = _Ring(interval=interval)
        self._lock = threading.Lock()
        self.snap = {}
        self._unit_at = 0.0
        self._last = {}

    def sample(self, now=None):
        now = time.time() if now is None else now
        gen = prm = None
        st, health = _get(f"{_INFER}/health")
        gen_done, live = None, None
        if _BACKEND in ("llama", "vllm", "tensorfold"):
            mst, mbody = _get(f"{_INFER}/metrics", auth=True)
            gen, prm = engine_counters(_BACKEND, mbody if mst == 200 else "",
                                       health if st == 200 else "")
        if _BACKEND == "llama" and gen is not None:
            gen_done = gen
            live = llama_inflight_tokens(*_get(f"{_INFER}/slots", auth=True))
            if live is not None:
                gen += live
        gate = {"enabled": _EDGE_GATE}
        g_req = g_err = g_comp = None
        if _EDGE_GATE:
            hst, hbody = _gate_get("/gate/health")
            gate["up"] = hst == 200
            try:
                h = json.loads(hbody) if hst == 200 else {}
            except ValueError:
                h = {}
            gate["auth"] = h.get("auth") if isinstance(h.get("auth"), str) else None
            gate["devices"] = h.get("devices") if isinstance(h.get("devices"), int) else None
            gate["active_devices"] = _active_devices()   # wall clock: the file is
            mst, mbody = _gate_get("/gate/metrics")
            if mst == 200:
                by = _prom_by(mbody, "openbeast_edge_requests_total", "outcome")
                g_req = sum(by.values())
                g_err = sum(v for k, v in by.items() if k != "ok")
                g_comp = _prom_sum(mbody, "openbeast_edge_completion_tokens_total")
                # denied_total counts EVERY refusal (rate limit, fanout,
                # path, size...); only no_key / unknown_key / revoked are
                # "no identity" (review 2026-11-22). Counts per reason only.
                by_reason = _prom_by(mbody, "openbeast_edge_denied_total", "reason")
                gate["denied_total"] = int(sum(by_reason.values()))
                gate["denied_identity"] = int(sum(v for k, v in by_reason.items() if k in _IDENTITY_REASONS))
                gate["denied_by_reason"] = {k[:32]: int(v) for k, v in sorted(by_reason.items())[:16]}
            gate["requests_total"] = int(g_req) if g_req is not None else None
            gate["errors_total"] = int(g_err) if g_err is not None else None
        self.ring.add({"gen": gen, "prompt": prm, "greq": g_req, "gcomp": g_comp}, now)
        # llama.cpp prints counters past 1e6 with 6 significant digits (a
        # 10-token step at 1.8e6, 100 at 1e7): widen the window so the step
        # stays a small part of the rate (review 2026-11-22).
        tok_s = _rate(self.ring, "gen", now, window=_counter_window(gen_done if gen_done is not None else gen))
        # llama.cpp adds a request's tokens to tokens_predicted_total only
        # when it FINISHES, so the live rate also counts each busy slot's
        # n_decoded from /slots; without /slots (--no-slots) the rate is
        # per completed request, and says so.
        source = "engine-completed" if gen_done is not None and live is None else "engine"
        if tok_s is None and gen is None:
            tok_s, source = _rate(self.ring, "gcomp", now, window=30.0), "gate"
        if tok_s is None:
            source = None
        rpm = _rate(self.ring, "greq", now, window=60.0)
        gate["req_per_min"] = round(rpm * 60, 1) if rpm is not None else None
        self.ring.set_last("tok_s", tok_s)
        if now - self._unit_at > 15 or "unit" not in self.snap:
            self._unit_at = now
            unit = inference_unit()
        else:
            unit = self.snap["unit"]
        island = island_state()
        prm_s = _rate(self.ring, "prompt", now, window=_counter_window(prm))
        with self._lock:
            self.snap = {"tok_s": round(tok_s, 1) if tok_s is not None else None,
                         "tok_s_source": source,
                         "prompt_tok_s": round(prm_s, 1) if prm_s is not None else None,
                         "gate": gate, "unit": unit, "interval_s": self.interval}
        self._transitions(_model_up((st, health)), unit, island, gate)

    def _transitions(self, healthy, unit, island, gate):
        cur = {"healthy": healthy, "unit": unit.get("name"), "island": island.get("state"),
               "gate": gate.get("up")}
        last, self._last = self._last, cur
        if not last:
            event(f"dashboard started: model {'healthy' if healthy else 'down'}"
                  + (f", {unit['name']}" if unit.get("name") else ""))
            return
        if last["healthy"] != healthy:
            event("model server healthy" if healthy else "model server DOWN",
                  "info" if healthy else "warn")
        if last["unit"] != cur["unit"]:
            event(f"inference unit: {cur['unit'] or 'none active'}")
        if last["island"] != cur["island"] and cur["island"]:
            event(f"beast-island: {cur['island']}", "warn" if cur["island"] == "islanded" else "info")
        if last["gate"] != cur["gate"] and cur["gate"] is not None:
            event("beast-gate up" if cur["gate"] else "beast-gate DOWN", "info" if cur["gate"] else "warn")

    def snapshot(self):
        with self._lock:
            out = dict(self.snap)
        out["history"] = {"tok_s": self.ring.series("tok_s"), "t": self.ring.times()}
        return out

    def loop(self):
        while True:
            t0 = time.monotonic()
            try:
                self.sample()
            except Exception:
                pass
            time.sleep(max(1.0, self.interval - (time.monotonic() - t0)))


def island_state():
    """beast-island from its state file: online / islanded, or stale."""
    try:
        with open(os.path.join(REPO_DIR, ".run", "island.json")) as fh:
            s = json.load(fh)
    except (OSError, ValueError):
        return {"state": None}
    if not isinstance(s, dict):
        return {"state": None}
    state = s.get("state") if isinstance(s.get("state"), str) else None
    upd, stale = s.get("updated_at"), s.get("stale_after")
    if isinstance(upd, (int, float)) and isinstance(stale, (int, float)) and time.time() - upd > stale:
        state = "stale"
    return {"state": state, "mode": s.get("mode") if isinstance(s.get("mode"), str) else None}


def _bool_env(*names):
    return any(os.environ.get(n, "").strip().lower() == "true" for n in names)


def _port_env(name, default):
    v = os.environ.get(name, "").strip()
    return int(v) if v.isdigit() else default


def _extra_service_requests():
    reqs = []
    if _bool_env("OPENBEAST_BEAST_CHAT", "BEAST_CHAT"):
        reqs.append((f"http://{H}:{_port_env('OPENBEAST_CHAT_PORT', 3003)}/api/chat/health", False))
    if _bool_env("OPENBEAST_BEAST_ARTIFACT", "BEAST_ARTIFACT"):
        reqs.append((f"http://{H}:{_port_env('OPENBEAST_ARTIFACT_PORT', 3004)}/api/artifacts/health", False))
    return reqs


def service_list(svc, gate):
    """Every service with its port and state, for the page. state: up,
    down, off (not enabled here), online / islanded / stale (beast-island)."""
    def row(name, label, port, state, detail=None):
        return {"name": name, "label": label, "port": port, "state": state, "detail": detail}
    model_port = _endpoint(_INFER).rsplit(":", 1)[-1]
    out = [row("model", ENGINE_LABEL.get(_BACKEND, _BACKEND), int(model_port) if model_port.isdigit() else None,
               "up" if svc.get("model") else "down")]
    gate_on = _EDGE_GATE
    out.append(row("gate", "beast-gate", _port_env("OPENBEAST_EDGE_PORT", 8090),
                   "off" if not gate_on else ("up" if gate.get("up") else "down" if gate.get("up") is False
                                              else "unknown")))
    out += [row("tools", "tool server", 3001, "up" if svc.get("tools") else "down"),
            row("webui", "Open WebUI", 3000, "up" if svc.get("webui") else "down"),
            row("search", "SearXNG", 8888, "up" if svc.get("search") else "down")]
    isl = island_state()
    out.append(row("island", "beast-island", None, isl["state"] or "unknown", isl.get("mode")))
    for name, label, env, port_env, port, path in (
            ("chat", "beast-chat", ("OPENBEAST_BEAST_CHAT", "BEAST_CHAT"), "OPENBEAST_CHAT_PORT", 3003,
             "/api/chat/health"),
            ("artifact", "beast-artifact", ("OPENBEAST_BEAST_ARTIFACT", "BEAST_ARTIFACT"),
             "OPENBEAST_ARTIFACT_PORT", 3004, "/api/artifacts/health")):
        p = _port_env(port_env, port)
        if not _bool_env(*env):
            out.append(row(name, label, p, "off"))
            continue
        st, _ = _fetch(f"http://{H}:{p}{path}")
        out.append(row(name, label, p, "up" if st == 200 else "down"))
    if _INSTINCT:
        out.append(row("instinct", "beast-instinct", _INSTINCT_PORT, "up" if svc.get("instinct") else "down"))
    out.append(row("dashboard", "dashboard", PORT, "up"))
    return out


def tool_breakdown(body):
    """Top tools by calls (summed over profiles; no per-user label exists or
    is kept), errors alongside."""
    calls, errs = {}, {}
    for line in body.splitlines():
        if not line.startswith("openbeast_tool_calls_total{"):
            continue
        m = re.search(r'tool="((?:[^"\\]|\\.)*)"', line)
        try:
            v = float(line.rsplit(" ", 1)[1])
        except (IndexError, ValueError):
            continue
        t = (m.group(1) if m else "?")[:48]
        calls[t] = calls.get(t, 0) + v
        if 'outcome="error"' in line:
            errs[t] = errs.get(t, 0) + v
    top = sorted(calls.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    return [{"tool": t, "calls": int(n), "errors": int(errs.get(t, 0))} for t, n in top]


_head = None
_peers = []
_serve = None
NODE_SCRIPT = ""


def cluster_status():
    """/api/cluster: this node and every peer, from the samplers' latest
    readings (a request never runs a probe)."""
    nodes = ([_head] if _head else []) + _peers
    name = _kv_file(os.path.join(REPO_DIR, "scripts", "backends", "cluster.env")).get("CLUSTER_NAME") if _peers else None
    price, cur = _kwh_price()
    return {"cluster": name or None, "generated_at": round(time.time()),
            "nodes": [n.snapshot() for n in nodes], "peers_configured": len(_peers),
            "power": power_summary([n.power_row() for n in nodes], price=price, currency=cur)}


def power_status(window):
    """/api/power?window=: the chart series of one window (CHART_WINDOWS)."""
    nodes = ([_head] if _head else []) + _peers
    return power_chart([n.power_row() for n in nodes], window)


def _power_saver(samplers):
    while True:
        time.sleep(POWER_SAVE_S)
        with contextlib.suppress(Exception):
            save_power(samplers)


def start_samplers():
    """Build the fixed probe and start every background sampler (main only:
    importing this module — the tests — starts nothing)."""
    global _head, _peers, _serve, NODE_SCRIPT
    NODE_SCRIPT = node_script(_disk_path())
    _head = NodeSampler(_head_name(), None, "head", 0 if discover_peers() else None)
    _peers = [NodeSampler(label, t, "peer", rank) for t, label, rank in discover_peers()]
    _serve = ServeSampler()
    nodes = [_head, *_peers]
    restored = load_power(nodes)
    if restored:
        event(f"power history restored for {restored} node{'s' if restored != 1 else ''}")
    for s in [*nodes, _serve]:
        threading.Thread(target=s.loop, daemon=True).start()
    threading.Thread(target=_power_saver, args=(nodes,), daemon=True).start()

    def _term(signum, frame):
        # ext.sh restart / stop.sh: keep the power history across it.
        with contextlib.suppress(Exception):
            save_power(nodes)
        os._exit(0)
    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGTERM, _term)


# The page and its assets: a fixed allowlist of files in static/, never a
# path built from the request.
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC = {"/static/app.css": ("app.css", "text/css; charset=utf-8", "no-cache"),
          "/static/app.js": ("app.js", "text/javascript; charset=utf-8", "no-cache"),
          "/static/geist.woff2": ("Geist-Variable.woff2", "font/woff2", "max-age=604800"),
          "/static/geist-mono.woff2": ("GeistMono-Variable.woff2", "font/woff2", "max-age=604800"),
          "/static/favicon.svg": ("favicon.svg", "image/svg+xml", "max-age=86400")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; "
       "img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")


def _read_static(name):
    try:
        with open(os.path.join(STATIC_DIR, name), "rb") as fh:
            return fh.read()
    except OSError:
        return None


# Read once at startup: a request never touches the filesystem for them.
_STATIC_BODIES = {path: _read_static(name) for path, (name, _, _) in STATIC.items()}


PAGE = (_read_static("index.html") or b"<!doctype html><title>OpenBeast</title>"
        b"<p>dashboard static/ files missing</p>").decode("utf-8")



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
# ...and so does one that TRICKLES: SOCKET_TIMEOUT_S bounds each recv, not
# the request, so a byte every 9 s held a handler forever and 16 of them
# answered every real caller 503. The request line plus headers must be in
# within this, whole.
HEADER_DEADLINE_S = 5.0


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
_cluster_cached = _Cached(lambda: cluster_status())
_power_cached = {w: _Cached((lambda w=w: power_status(w)), ttl=2.0) for w in CHART_WINDOWS}


def _finite(o):
    """NaN / Infinity -> null, anywhere in a document: they are not JSON."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def _json(doc):
    try:
        return json.dumps(doc, allow_nan=False).encode()
    except ValueError:
        return json.dumps(_finite(doc), allow_nan=False).encode()


class Handler(BaseHTTPRequestHandler):
    timeout = SOCKET_TIMEOUT_S
    header_deadline = HEADER_DEADLINE_S

    def log_message(self, *a):  # quiet
        pass

    def setup(self):
        super().setup()
        # Cut the connection if the request is not fully read by the deadline;
        # parse_request() returning is "fully read" (HTTP/1.0: one request).
        self._header_timer = threading.Timer(self.header_deadline, self._too_slow)
        self._header_timer.daemon = True
        self._header_timer.start()

    def _too_slow(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def parse_request(self):
        ok = super().parse_request()
        self._header_timer.cancel()
        return ok

    def finish(self):
        self._header_timer.cancel()
        super().finish()

    def do_GET(self):
        # /api/slot is the tailnet discovery contract, published to every
        # device and read by whatever name a client dials: never pinned.
        # The page and /api/status are rig-local, and Host-pinned.
        if (not self.path.startswith("/api/slot")
                and not host_ok(self.headers.get_all("Host") or [])):
            body = (b"Invalid host header: the dashboard page answers only to "
                    b"loopback, this machine's name, *.ts.net names and IP "
                    b"literals. Another name: OPENBEAST_DASHBOARD_ALLOWED_HOSTS.\n")
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        path = self.path.split("?", 1)[0]
        if self.path.startswith("/api/slot"):
            body = json.dumps(_slot_cached.get()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif path == "/api/status":
            body = _json(_status_cached.get())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        elif path == "/api/cluster":
            body = _json(_cluster_cached.get())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        elif path == "/api/power":
            # The window is looked up in a fixed table; any other value is
            # the default. Nothing from the query reaches anything else.
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            w = (q.get("window") or ["15m"])[0]
            body = _json(_power_cached.get(w, _power_cached["15m"]).get())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        elif path in ("/", "/index.html"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Security-Policy", CSP)
        elif path in STATIC and _STATIC_BODIES.get(path) is not None:
            body = _STATIC_BODIES[path]
            self.send_response(200)
            self.send_header("Content-Type", STATIC[path][1])
            self.send_header("Cache-Control", STATIC[path][2])
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_only(self):
        # GET (and HEAD's absence) only: the dashboard changes nothing, so
        # every other method is refused before any route runs.
        body = b"read-only: GET only\n"
        self.send_response(405)
        self.send_header("Allow", "GET")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _read_only

    def __getattr__(self, name):
        # Any other method (TRACE, PROPFIND, a made-up verb) is the same 405,
        # not http.server's 501.
        if name.startswith("do_"):
            return self._read_only
        raise AttributeError(name)


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
    start_samplers()
    if _peers:
        print("  peers: " + ", ".join(f"{p.name} ({p.target})" for p in _peers), flush=True)
    BoundedServer((BIND, PORT), Handler).serve_forever()
