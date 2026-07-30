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
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_DIR = os.environ.get("OPENBEAST_REPO_DIR", ".")
BIND = os.environ.get("OPENBEAST_BIND", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.environ.get("DASHBOARD_PORT", "3002"))
# Probes always target loopback — the dashboard runs on the same box as the
# stack; BIND only controls who can reach the dashboard itself.
H = "127.0.0.1"


# Keyed llama-server (LLAMA_API_KEY): the dashboard's own probes must present
# the bearer. start.sh's extension launcher inherits the conf.sh export;
# a standalone `run.sh` on a keyless stack sends nothing, unchanged.
_API_KEY = os.environ.get("OPENBEAST_API_KEY", "").strip()


def _get(url, timeout=2, auth=False):
    try:
        req = urllib.request.Request(url)
        if auth and _API_KEY:
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


def model_status():
    ok = _get(f"http://{H}:8080/health")[0] == 200
    served = ""
    try:
        served = open(os.path.join(REPO_DIR, ".run", "serve-script")).read().strip()
    except Exception:
        pass
    alias = ""
    st, body = _get(f"http://{H}:8080/v1/models", auth=True)
    if st == 200:
        try:
            alias = json.loads(body)["data"][0].get("id", "")
        except Exception:
            pass
    return {"healthy": ok, "serve_script": served, "alias": alias}


def services_status():
    svc = [
        ("model", f"http://{H}:8080/health", "ok"),
        ("tools", f"http://{H}:8080".replace("8080", "3001") + "/health", "ok"),
        ("webui", f"http://{H}:3000/api/version", "version"),
        ("search", f"http://{H}:8888/", ""),
    ]
    out = {}
    for name, url, needle in svc:
        st, body = _get(url)
        out[name] = bool(st and st < 500 and (needle in body if needle else True))
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
    return {"gpu": gpu_status(), "model": model_status(),
            "services": services_status(), "metrics": tool_metrics()}


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
    st, body = _get(f"http://{H}:8080/metrics", auth=True)
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
    ok = _get(f"http://{H}:8080/health")[0] == 200
    model = {"id": None, "ctx": None}
    slots = {"total": None, "busy": None}
    st, body = _get(f"http://{H}:8080/v1/models", auth=True)
    if st == 200:
        try:
            model["id"] = json.loads(body)["data"][0].get("id") or None
        except Exception:
            pass
    st, body = _get(f"http://{H}:8080/props", auth=True)
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
    st, body = _get(f"http://{H}:8080/slots", auth=True)
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
        "services": services_status(),
        "auth": "key" if _API_KEY else "open",
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        if self.path.startswith("/api/slot"):
            body = json.dumps(slot_status()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path.startswith("/api/status"):
            body = json.dumps(status()).encode()
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


if __name__ == "__main__":
    print(f"OpenBeast dashboard on http://{BIND}:{PORT}", flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
