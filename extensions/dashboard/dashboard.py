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


def _get(url, timeout=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
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
    st, body = _get(f"http://{H}:8080/v1/models")
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
        if self.path.startswith("/api/status"):
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
