#!/usr/bin/env python3
"""Dashboard v2 (extensions/dashboard): cluster view, peer collection, serving
and gate detail, the read-only HTTP surface, the static page, and the
/api/slot contract kept byte-identical (golden).

Peer metrics come from one fixed script run by a background sampler over
`ssh -o BatchMode=yes`; these tests mock ssh and nvidia-smi output, and pin
that no HTTP request ever runs a probe.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
import re
import shutil
import subprocess
import threading
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(REPO, "extensions", "dashboard")
_PATH = os.path.join(DASH, "dashboard.py")
_ENV = ("OPENBEAST_PROBE_HOST", "OPENBEAST_INFERENCE_URL", "OPENBEAST_INFERENCE_BACKEND",
        "OPENBEAST_INFERENCE_SLOTS", "OPENBEAST_API_KEY", "EDGE_GATE", "OPENBEAST_EDGE_ALLOW_ANON",
        "OPENBEAST_INSTINCT", "OPENBEAST_INSTINCT_PORT", "DASHBOARD_ALLOWED_HOSTS",
        "OPENBEAST_DASHBOARD_ALLOWED_HOSTS", "OPENBEAST_DASHBOARD_PEERS", "DASHBOARD_PEERS",
        "OPENBEAST_EDGE_MAX_INFLIGHT", "OPENBEAST_EDGE_RATE_LIMIT", "OPENBEAST_BEAST_CHAT", "BEAST_CHAT",
        "OPENBEAST_BEAST_ARTIFACT", "BEAST_ARTIFACT", "OPENBEAST_EDGE_PORT", "OPENBEAST_DASHBOARD_DISK_PATH",
        "DASHBOARD_DISK_PATH")


def load(monkeypatch, repo=None, **env):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("OPENBEAST_REPO_DIR", str(repo) if repo else "/nonexistent-openbeast")
    spec = importlib.util.spec_from_file_location("dashboard_v2", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._kv_unified = lambda: True
    return mod


# ───────────────────────── canned node outputs (NODE_SCRIPT) ─────────────────────────

GB10 = """host|spark-3dc6
gpu|NVIDIA GB10, [N/A], [N/A], 95, 61, 31.34
load|3.24 3.13 3.02 4/1061 68055
ncpu|20
stat|cpu  1000 0 500 8000 100 0 0 0 0 0
mem|MemTotal:       127598700 kB
mem|MemAvailable:   14455296 kB
uptime|379205.06
disk|/dev/nvme0n1p2 3937374564 1552956340 2087340368 43% /
ib|rocep1s0f0|1|4: ACTIVE|1000|2000|enp1s0f0np0|9000|200 Gb/sec (4X HDR)
ib|rocep1s0f1|1|1: DOWN|0|0|enp1s0f1np1|9000|40 Gb/sec (4X QDR)
"""
GB10_LATER = GB10.replace("stat|cpu  1000 0 500 8000 100", "stat|cpu  1300 0 600 8500 100") \
                 .replace("|1000|2000|enp1s0f0np0", "|126000|252000|enp1s0f0np0")
RTX = """host|beast
gpu|NVIDIA GeForce RTX 5090, 27497, 32607, 41, 52, 318.40
ncpu|32
mem|MemTotal:       65536000 kB
mem|MemAvailable:   43536000 kB
uptime|266400.5
disk|/dev/nvme1n1 3937374564 1552956340 2087340368 43% /
"""
NOGPU = """host|laptop.local
ncpu|10
disk|/dev/disk3s5 1948455792 1454125060 447760724 77% /System/Volumes/Data
"""


def test_parse_gb10_is_unified_memory_with_fabric_rates(monkeypatch):
    d = load(monkeypatch)
    first = d.node_view(d.parse_node(GB10))
    g = first["gpu"]
    assert g["name"] == "NVIDIA GB10" and g["unified"] is True
    # unified: the pool IS host RAM (MemTotal - MemAvailable), never "[N/A]"
    assert g["mem_total_mib"] == 127598700 // 1024 and g["mem_used_mib"] == 127598700 // 1024 - 14455296 // 1024
    assert g["util_pct"] == 95 and g["temp_c"] == 61 and g["power_w"] == 31.3
    assert first["cpu"]["ncpu"] == 20 and first["cpu"]["load"] == [3.24, 3.13, 3.02]
    assert first["cpu"]["util_pct"] is None            # a rate needs two readings
    assert first["disk"]["total_b"] == 3937374564 * 1024 and first["uptime_s"] == 379205.06
    up = [p for p in first["fabric"] if p["state"] == "ACTIVE"]
    assert up[0]["netdev"] == "enp1s0f0np0" and up[0]["mtu"] == 9000 and up[0]["rate"].startswith("200 Gb/sec")
    assert up[0]["tx_bps"] is None
    later = d.node_view(d.parse_node(GB10_LATER), d.parse_node(GB10), 5.0)
    assert later["cpu"]["util_pct"] == round(100 * (1 - 500 / 900), 1)
    p = [x for x in later["fabric"] if x["state"] == "ACTIVE"][0]
    # port_*_data counts 4-octet words: (126000-1000)*4 bytes * 8 bits / 5 s
    assert p["tx_bps"] == 125000 * 4 * 8 / 5 and p["rx_bps"] == 250000 * 4 * 8 / 5


def test_parse_rtx_is_discrete_vram_not_unified(monkeypatch):
    d = load(monkeypatch)
    v = d.node_view(d.parse_node(RTX))
    g = v["gpu"]
    assert g["unified"] is False and g["mem_used_mib"] == 27497 and g["mem_total_mib"] == 32607
    assert g["mem_pct"] == round(100 * 27497 / 32607, 1) and g["power_w"] == 318.4
    assert v["ram"]["total_mib"] == 64000 and v["fabric"] == []


def test_parse_no_gpu_degrades_to_nulls(monkeypatch):
    d = load(monkeypatch)
    v = d.node_view(d.parse_node(NOGPU))
    assert v["gpu"] is None and v["ram"] is None and v["uptime_s"] is None
    assert v["cpu"]["ncpu"] == 10 and v["disk"]["pct"] is not None
    assert d.node_view(d.parse_node("")) == {"hostname": None, "gpu": None,
                                             "cpu": {"load": None, "ncpu": None, "util_pct": None},
                                             "ram": None, "disk": None, "uptime_s": None, "fabric": []}
    # junk never raises
    d.node_view(d.parse_node("gpu|a\nload|x\nstat|cpu\nmem|MemTotal:\ndisk|a b\nib|a|b\n" + "\x00" * 10))


def test_node_script_runs_locally_and_is_fixed(monkeypatch, tmp_path):
    d = load(monkeypatch)
    s = d.node_script("~/openbeast-models")
    assert 'D="$HOME/openbeast-models"' in s
    # a hostile configured path never reaches the script
    for bad in ("/x; rm -rf ~", "$(id)", "/a`b`", "../../etc", "/a b"):
        assert "id)" not in d.node_script(bad) and "rm -rf" not in d.node_script(bad)
        assert 'D="$HOME"' in d.node_script(bad)
    r = subprocess.run(["sh", "-s"], input=d.node_script(str(tmp_path)), capture_output=True,
                       text=True, timeout=20)
    assert r.returncode == 0
    v = d.node_view(d.parse_node(r.stdout))
    assert v["hostname"] and v["disk"] and v["disk"]["total_b"] > 0


# ───────────────────────── peers: discovery and collection ─────────────────────────

def test_peers_from_the_pair_config(monkeypatch, tmp_path):
    (tmp_path / "scripts" / "backends").mkdir(parents=True)
    (tmp_path / "scripts" / "backends" / "cluster.env").write_text(
        'CLUSTER_NODES="local max@10.100.216.2"\nCLUSTER_NAME=spark-hydra\n')
    d = load(monkeypatch, repo=tmp_path)
    assert d.discover_peers(aliases={"10.100.216.2": "spark-2"}) == [("max@10.100.216.2", "spark-2", 1)]
    assert d.discover_peers(aliases={}) == [("max@10.100.216.2", "10.100.216.2", 1)]


def test_peers_setting_wins_and_none_disables(monkeypatch, tmp_path):
    (tmp_path / "scripts" / "backends").mkdir(parents=True)
    (tmp_path / "scripts" / "backends" / "cluster.env").write_text('CLUSTER_NODES="local max@10.0.0.2"\n')
    d = load(monkeypatch, repo=tmp_path, DASHBOARD_PEERS="spark-2, spark-3")
    assert [p[0] for p in d.discover_peers(aliases={})] == ["spark-2", "spark-3"]
    d = load(monkeypatch, repo=tmp_path, OPENBEAST_DASHBOARD_PEERS="none")
    assert d.discover_peers(aliases={}) == []
    (tmp_path / "openbeast.conf").write_text('DASHBOARD_PEERS="spark-9"\n')
    d = load(monkeypatch, repo=tmp_path)
    assert d.discover_peers(aliases={}) == [("spark-9", "spark-9", None)]


def test_a_rig_with_no_pair_config_has_no_peers(monkeypatch, tmp_path):
    d = load(monkeypatch, repo=tmp_path)
    assert d.discover_peers(aliases={}) == []


@pytest.mark.parametrize("bad", ["-oProxyCommand=touch /tmp/x", "a;b", "$(id)", "a b"])
def test_a_peer_target_can_never_be_an_ssh_option(monkeypatch, tmp_path, bad):
    d = load(monkeypatch, repo=tmp_path, DASHBOARD_PEERS=bad)
    assert all(not t.startswith("-") and re.match(r"^[\w.@-]+$", t) for t, _, _ in d.discover_peers(aliases={}))


def test_ssh_aliases_map_an_address_to_its_name(monkeypatch, tmp_path):
    d = load(monkeypatch)
    cfg = tmp_path / "config"
    cfg.write_text("Host *\n  ServerAliveInterval 5\nHost spark-2\n  ### CreatedBy: NVIDIA Sync\n"
                   "  Hostname 10.100.216.2\n  User max\n")
    assert d._ssh_aliases(str(cfg)) == {"10.100.216.2": "spark-2"}


def test_peer_ssh_is_batch_mode_capped_and_fixed(monkeypatch):
    d = load(monkeypatch)
    d.NODE_SCRIPT = d.node_script("~/m")
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, stdout=GB10, stderr="")
    monkeypatch.setattr(d.subprocess, "run", run)
    out, err = d._run_node("max@10.100.216.2")
    assert err is None and out == GB10
    cmd = seen["cmd"]
    assert cmd[0] == "ssh" and "BatchMode=yes" in cmd and "ConnectTimeout=3" in cmd
    assert cmd[-3:] == ["--", "max@10.100.216.2", "sh -s"]
    assert seen["kw"]["input"] == d.NODE_SCRIPT and seen["kw"]["timeout"] == d.PEER_TIMEOUT_S


@pytest.mark.parametrize("outcome,want", [
    ("timeout", "no answer within"), ("rc255", "ssh failed"), ("empty", "empty answer"), ("nossh", "ssh not available")])
def test_an_unreachable_peer_reads_unreachable(monkeypatch, outcome, want):
    d = load(monkeypatch)

    def run(cmd, **kw):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        if outcome == "nossh":
            raise FileNotFoundError("ssh")
        return subprocess.CompletedProcess(cmd, 255 if outcome == "rc255" else 0, stdout="", stderr="secret-ish")
    monkeypatch.setattr(d.subprocess, "run", run)
    s = d.NodeSampler("spark-2", "spark-2", "peer", 1)
    s.sample()
    snap = s.snapshot()
    assert snap["state"] == "unreachable" and want in snap["error"] and "secret" not in snap["error"]
    assert snap["gpu"] is None and snap["fabric"] == []


def test_a_peer_that_comes_back_is_up_with_rates_and_events(monkeypatch):
    d = load(monkeypatch)
    answers = iter([(None, "ssh failed"), (GB10, None), (GB10_LATER, None)])
    s = d.NodeSampler("spark-2", "spark-2", "peer", 1)
    for _ in range(3):
        a = next(answers)
        s.sample(run=lambda t, a=a: a)
    snap = s.snapshot()
    assert snap["state"] == "up" and snap["error"] is None and snap["gpu"]["unified"] is True
    assert snap["cpu"]["util_pct"] is not None
    assert len(snap["history"]["util"]) == 2 and snap["history"]["util"][-1] == 95
    texts = [e["text"] for e in d.events()]
    assert any("spark-2 unreachable" in t for t in texts) and any("spark-2 is back" in t for t in texts)


# ───────────────────────── HTTP: read-only, pinned, never probes ─────────────────────────

class Live:
    def __init__(self, d):
        self.srv = d.BoundedServer(("127.0.0.1", 0), d.Handler)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.t.start()

    def req(self, method, path, host="127.0.0.1"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            c.request(method, path, headers={"Host": host})
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.t.join(5)


def _no_upstreams(d, monkeypatch):
    d._get = lambda url, timeout=2, auth=False: (None, "")
    d.gpu_status = lambda: None

    def boom(*a, **kw):
        raise AssertionError(f"a request ran a subprocess: {a!r}")
    monkeypatch.setattr(d.subprocess, "run", boom)


def test_api_cluster_serves_the_samplers_snapshot_without_probing(monkeypatch):
    d = load(monkeypatch)
    _no_upstreams(d, monkeypatch)
    head = d.NodeSampler("spark-1", None, "head", 0)
    head.sample(run=lambda t: (GB10, None))
    peer = d.NodeSampler("spark-2", "spark-2", "peer", 1)
    peer.sample(run=lambda t: (None, "ssh failed (unreachable, or key login refused)"))
    d._head, d._peers = head, [peer]
    live = Live(d)
    try:
        st, hdr, body = live.req("GET", "/api/cluster", "spark-1.tail4109f9.ts.net:8447")
        assert st == 200 and hdr["Content-Type"] == "application/json"
        doc = json.loads(body)
        assert [n["name"] for n in doc["nodes"]] == ["spark-1", "spark-2"]
        assert [n["state"] for n in doc["nodes"]] == ["up", "unreachable"]
        assert doc["nodes"][0]["gpu"]["unified"] is True and doc["peers_configured"] == 1
        # the page and /api/status never run a probe either
        assert live.req("GET", "/", "127.0.0.1")[0] == 200
        assert live.req("GET", "/api/status", "127.0.0.1")[0] == 200
    finally:
        live.close()


@pytest.mark.parametrize("path", ["/api/cluster", "/api/status", "/", "/static/app.js"])
def test_rig_local_routes_stay_host_pinned(monkeypatch, path):
    d = load(monkeypatch)
    _no_upstreams(d, monkeypatch)
    live = Live(d)
    try:
        assert live.req("GET", path, "evil.example:3002")[0] == 400
        assert live.req("GET", path, "spark-1.tail4109f9.ts.net")[0] == 200
    finally:
        live.close()


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
@pytest.mark.parametrize("path", ["/", "/api/status", "/api/cluster", "/api/slot"])
def test_get_only(monkeypatch, method, path):
    d = load(monkeypatch)
    _no_upstreams(d, monkeypatch)
    live = Live(d)
    try:
        st, hdr, _ = live.req(method, path)
        assert st == 405 and hdr.get("Allow") == "GET"
    finally:
        live.close()


def test_static_files_are_an_allowlist(monkeypatch):
    d = load(monkeypatch)
    _no_upstreams(d, monkeypatch)
    live = Live(d)
    try:
        for p, (_, ctype, _) in d.STATIC.items():
            st, hdr, body = live.req("GET", p)
            assert st == 200 and hdr["Content-Type"] == ctype and body, p
        for p in ("/static/../dashboard.py", "/static/dashboard.py", "/static/GEIST-OFL.txt",
                  "/static/index.html/..", "/etc/passwd", "/static/%2e%2e/dashboard.py"):
            assert live.req("GET", p)[0] == 404, p
        st, hdr, body = live.req("GET", "/")
        csp = hdr["Content-Security-Policy"]
        assert "default-src 'none'" in csp and "script-src 'self'" in csp and "'unsafe-inline'" not in csp
        assert hdr["X-Content-Type-Options"] == "nosniff"
    finally:
        live.close()


def test_the_page_is_self_contained(monkeypatch):
    # Offline, and nothing an external host could change: no CDN, no font
    # service, no remote image. (The SVG namespace is an identifier, not a fetch.)
    for name in ("index.html", "app.css", "app.js", "favicon.svg"):
        src = open(os.path.join(DASH, "static", name)).read()
        urls = [u for u in re.findall(r"(?:https?:)?//[\w.-]+\.\w+[^\s\"')]*", src)
                if not u.startswith("http://www.w3.org/2000/svg")]
        assert not urls, (name, urls)
        assert "<style" not in src and " style=" not in src and "onclick" not in src.lower(), name
    html = open(os.path.join(DASH, "static", "index.html")).read()
    assert "<script>" not in html and "🦁" not in html and "OB" in html
    total = sum(os.path.getsize(os.path.join(DASH, "static", f)) for f in os.listdir(os.path.join(DASH, "static")))
    assert total < 400_000, total            # modest weight: two variable fonts + the page


# ───────────────────────── /api/slot: byte-identical (golden) ─────────────────────────

_SLOT_ANS = {
    "llama": {"/health": (200, '{"status":"ok"}'), "/v1/models": (200, '{"data":[{"id":"qwen"}]}'),
              "/props": (200, '{"total_slots":1,"default_generation_settings":{"n_ctx":262144}}'),
              "/slots": (200, '[{"id":0,"n_ctx":262144,"is_processing":true}]'),
              "/metrics": (200, "llamacpp:requests_deferred 2\n"), ":3001/health": (200, '{"status":"ok"}'),
              "/api/version": (200, '{"version":"1"}'), ":8888/": (200, "")},
    "vllm": {"/health": (200, ""), "/v1/models": (200, '{"data":[{"id":"Hy4-flash-NVFP4","max_model_len":262144}]}'),
             "/metrics": (200, 'vllm:num_requests_running{engine="0",model_name="m"} 3.0\n'
                               'vllm:num_requests_waiting{engine="0",model_name="m"} 1.0\n'
                               'vllm:kv_cache_usage_perc{engine="0",model_name="m"} 0.25\n'),
             ":3001/health": (200, '{"status":"ok"}'), "/api/version": (200, '{"version":"1"}'), ":8888/": (200, "")},
    "tensorfold": {"/health": (200, json.dumps({"ok": True, "requests_running": 2, "streams": {"max": 4},
                                                "pool_tokens": 1000, "pool_free_tokens": 250, "iteration_s": 0.4,
                                                "context_length": 1048576})),
                   "/v1/models": (200, '{"data":[{"id":"deepseek"}]}'), ":3001/health": (200, '{"status":"ok"}'),
                   "/api/version": (200, '{"version":"1"}'), ":8888/": (200, "")},
}
_SLOT_ENV = {"llama": {},
             "vllm": {"OPENBEAST_INFERENCE_BACKEND": "vllm", "OPENBEAST_INFERENCE_URL": "http://127.0.0.1:8000",
                      "OPENBEAST_INFERENCE_SLOTS": "4", "EDGE_GATE": "true"},
             "tensorfold": {"OPENBEAST_INFERENCE_BACKEND": "tensorfold", "OPENBEAST_INFERENCE_URL": "http://127.0.0.1:8000",
                            "EDGE_GATE": "true", "OPENBEAST_EDGE_MAX_INFLIGHT": "3"}}
# Produced by the pre-v2 dashboard (origin/main a6ad7f87) from the same canned
# upstreams: the exact bytes a client parses. A v2 change must not move one.
_SLOT_GOLDEN = {
    "llama": '{"beast_slot": 2, "min_client": 1, "healthy": true, "model": {"id": "qwen", "ctx": 262144}, "slots": {"total": 1, "busy": 1}, "capacity": {"ctx_shared": true, "ctx_total": 262144, "queue_deferred": 2, "serving_profile": "mtp-single-slot"}, "services": {"model": true, "tools": true, "webui": true, "search": true}, "auth": "open"}',
    "vllm": '{"beast_slot": 2, "min_client": 1, "healthy": true, "backend": "vllm", "model": {"id": "Hy4-flash-NVFP4", "ctx": 262144}, "slots": {"total": 4, "busy": 3}, "capacity": {"ctx_shared": true, "ctx_total": null, "queue_deferred": 1, "kv_usage": 0.25, "serving_profile": "batched-multi-slot"}, "services": {"model": true, "tools": true, "webui": true, "search": true}, "auth": "device", "admission": {"per_device_inflight": 4, "per_device_rate_per_min": 120}}',
    "tensorfold": '{"beast_slot": 2, "min_client": 1, "healthy": true, "backend": "tensorfold", "model": {"id": "deepseek", "ctx": 1048576}, "slots": {"total": 4, "busy": 2}, "capacity": {"ctx_shared": true, "ctx_total": 1000, "queue_deferred": null, "kv_usage": 0.75, "serving_profile": "batched-multi-slot"}, "services": {"model": true, "tools": true, "webui": true, "search": true}, "auth": "device", "admission": {"per_device_inflight": 3, "per_device_rate_per_min": 120}}',
}


def _canned(answers):
    def get(url, timeout=2, auth=False):
        for suf in sorted(answers, key=len, reverse=True):     # the most specific suffix wins
            if url.endswith(suf):
                return answers[suf]
        return None, ""
    return get


@pytest.mark.parametrize("backend", ["llama", "vllm", "tensorfold"])
def test_api_slot_is_byte_identical_to_v1_dashboard(monkeypatch, backend):
    d = load(monkeypatch, **_SLOT_ENV[backend])
    d._get = _canned(_SLOT_ANS[backend])
    assert json.dumps(d.slot_status()) == _SLOT_GOLDEN[backend]
    # ...and over HTTP, from any Host, with the samplers' state irrelevant
    live = Live(d)
    try:
        st, hdr, body = live.req("GET", "/api/slot", "client.lan:8444")
        assert st == 200 and body.decode() == _SLOT_GOLDEN[backend]
        assert hdr["Content-Type"] == "application/json" and "Cache-Control" not in hdr
    finally:
        live.close()


# ───────────────────────── /api/status v2 ─────────────────────────

def test_status_keeps_v1_keys_and_adds_detail(monkeypatch, tmp_path):
    (tmp_path / ".run").mkdir()
    (tmp_path / ".run" / "island.json").write_text(json.dumps(
        {"state": "online", "mode": "auto", "updated_at": time.time(), "stale_after": 100}))
    d = load(monkeypatch, repo=tmp_path, **_SLOT_ENV["vllm"], OPENBEAST_BEAST_CHAT="true")
    seen = []
    base = _canned(dict(_SLOT_ANS["vllm"], **{
        "/api/chat/health": (200, '{"status":"ok"}'),
        ":3001/metrics": (200, 'openbeast_tool_calls_total{tool="run_shell",profile="admin",outcome="ok"} 5\n'
                               'openbeast_tool_calls_total{tool="run_shell",profile="user",outcome="error"} 2\n'
                               'openbeast_tool_calls_total{tool="read_file",profile="user",outcome="ok"} 3\n')}))

    def get(url, timeout=2, auth=False):
        seen.append(url)
        return base(url, timeout, auth)
    d._get = get
    d.gpu_status = lambda: None
    out = d.status()
    assert {"gpu", "model", "services", "metrics"} <= set(out)
    assert sum(u.endswith(":8000/health") for u in seen) == 1, "one /health per document, still"
    assert out["model"]["engine"] == "vLLM" and out["model"]["id"] == "Hy4-flash-NVFP4" and out["model"]["ctx"] == 262144
    sv = out["serving"]
    assert sv["slots"] == {"total": 4, "busy": 3} and sv["requests_waiting"] == 1 and sv["kv_usage"] == 0.25
    assert out["metrics"]["top_tools"] == [{"tool": "run_shell", "calls": 7, "errors": 2},
                                           {"tool": "read_file", "calls": 3, "errors": 0}]
    assert "profile" not in json.dumps(out["metrics"]) and "admin" not in json.dumps(out)
    svc = {s["name"]: s for s in out["service_list"]}
    assert set(svc) >= {"model", "gate", "tools", "webui", "search", "island", "chat", "artifact"}
    assert svc["model"]["port"] == 8000 and svc["model"]["state"] == "up"
    assert svc["island"]["state"] == "online" and svc["chat"]["state"] == "up" and svc["chat"]["port"] == 3003
    assert svc["artifact"]["state"] == "off"


def test_gate_counts_never_carry_device_names(monkeypatch, tmp_path):
    (tmp_path / ".run").mkdir()
    now = time.time()
    stamp = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    (tmp_path / ".run" / "clients-lastseen.json").write_text(json.dumps(
        {"max-laptop": stamp(now - 60), "old-phone": stamp(now - 86400)}))
    d = load(monkeypatch, repo=tmp_path, EDGE_GATE="true")
    metrics = ('openbeast_edge_requests_total{device="max-laptop",path="/v1/chat/completions",outcome="ok"} 40\n'
               'openbeast_edge_requests_total{device="max-laptop",path="/v1/chat/completions",outcome="rate_limited"} 2\n'
               'openbeast_edge_denied_total{reason="no_key"} 3\n'
               'openbeast_edge_completion_tokens_total{device="max-laptop"} 900\n')
    gate = {"/gate/health": (200, '{"status":"ok","auth":"devices","devices":2,"upstream":"x"}'),
            "/gate/metrics": (200, metrics)}
    d._gate_get = lambda path: gate[path]
    d._get = _canned({"/health": (200, '{"status":"ok"}'),
                      "/metrics": (200, "llamacpp:tokens_predicted_total 1000\nllamacpp:prompt_tokens_total 50\n")})
    d.inference_unit = lambda units=None: {"name": "llama-server", "active": True, "uptime_s": 60}
    s = d.ServeSampler()
    s.sample(now=1000.0)
    d._get = _canned({"/health": (200, '{"status":"ok"}'),
                      "/metrics": (200, "llamacpp:tokens_predicted_total 1600\nllamacpp:prompt_tokens_total 150\n")})
    gate["/gate/metrics"] = (200, metrics.replace("} 40", "} 70"))
    s.sample(now=1010.0)
    snap = s.snapshot()
    g = snap["gate"]
    assert g == {"enabled": True, "up": True, "auth": "devices", "devices": 2, "active_devices": 1,
                 "denied_total": 3, "requests_total": 72, "errors_total": 2, "req_per_min": 180.0}
    assert snap["tok_s"] == 60.0 and snap["prompt_tok_s"] == 10.0 and snap["tok_s_source"] == "engine"
    assert snap["history"]["tok_s"] == [None, 60.0]
    assert "laptop" not in json.dumps(snap) and "phone" not in json.dumps(snap)


def test_engine_counters(monkeypatch):
    d = load(monkeypatch)
    vb = ('vllm:generation_tokens_total{engine="0",model_name="m"} 162550.0\n'
          'vllm:prompt_tokens_total{engine="0",model_name="m"} 3.66e+06\n')
    assert d.engine_counters("vllm", vb) == (162550.0, 3.66e6)
    assert d.engine_counters("llama", "llamacpp:tokens_predicted_total 1.83e+06\n") == (1.83e6, None)
    assert d.engine_counters("tensorfold", "", '{"ok":true,"generation_tokens_total":77}') == (77.0, None)
    assert d.engine_counters("tensorfold", "", "not json") == (None, None)


def test_a_counter_reset_is_no_rate(monkeypatch):
    d = load(monkeypatch)
    r = d._Ring()
    r.add({"gen": 500.0}, 0.0)
    r.add({"gen": 10.0}, 10.0)
    assert d._rate(r, "gen", 10.0) is None


def test_inference_unit_from_systemd(monkeypatch):
    d = load(monkeypatch, OPENBEAST_INFERENCE_BACKEND="vllm")
    monkeypatch.setattr(d, "_proc_uptime", lambda: 1000.0)
    units = {"deepseek-v41-inference": ("inactive", 0), "hy4-vllm-inference": ("active", 400_000_000)}
    assert d.inference_unit(units) == {"name": "hy4-vllm-inference", "active": True, "uptime_s": 600}
    assert d.inference_unit({})["name"] is None


# ───────────────────────── the page script (node) ─────────────────────────

def _js():
    return open(os.path.join(DASH, "static", "app.js")).read()


def test_page_never_writes_html():
    src = re.sub(r"//[^\n]*", "", _js())          # comments may name what is banned
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert banned not in src, banned


_FAKE_DOM = r"""
class El {
  constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.style = {};
    this.attrs = {}; this._t = ''; this.hidden = false; this.className = ''; }
  get textContent() { return this._t + this.children.map(c => c.textContent).join(''); }
  set textContent(v) { this._t = String(v); this.children = []; }
  set innerHTML(v) { throw new Error('innerHTML written'); }
  appendChild(c) { this.removeChild(c, true); this.children.push(c); c.parentNode = this; return c; }
  insertBefore(c, ref) { this.removeChild(c, true); const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) this.children.push(c); else this.children.splice(i, 0, c); c.parentNode = this; return c; }
  removeChild(c, quiet) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener() {}
  matches(sel) { if (sel.startsWith('.')) return this.className.split(' ').includes(sel.slice(1));
    return this.tagName === sel; }
  all() { return this.children.flatMap(c => [c, ...c.all()]); }
  querySelector(sel) { const parts = sel.split(' > ');
    if (parts.length === 2) { const p = this.all().find(e => e.matches(parts[0]));
      return p ? p.children.find(e => e.matches(parts[1])) || null : null; }
    return this.all().find(e => e.matches(sel)) || null; }
  html() { return '<' + this.tagName + '>' + this._t + this.children.map(c => c.html()).join('') + '</' + this.tagName + '>'; }
}
const els = {};
globalThis.document = {
  getElementById: id => els[id] || (els[id] = new El('div')),
  createElement: t => new El(t), createElementNS: (ns, t) => new El(t),
};
globalThis.__OBDASH_TEST__ = true;
"""


def _node(script, tmp_path, data):
    js = tmp_path / "t.js"
    js.write_text(_FAKE_DOM + _js() + "\nconst D = JSON.parse(process.env.DATA);\n" + script)
    r = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30,
                       env={"PATH": os.environ["PATH"], "DATA": json.dumps(data)})
    assert r.returncode == 0, r.stderr
    return r.stdout


_XSS = "<img src=x onerror=alert(1)>"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_hostile_strings_render_as_text(tmp_path):
    status = {"model": {"healthy": True, "id": _XSS, "engine": _XSS, "endpoint": _XSS,
                        "unit": {"name": _XSS, "uptime_s": 5}},
              "serving": {"slots": {"total": 4, "busy": 1}, "capacity": {"serving_profile": _XSS},
                          "history": {"tok_s": [1, None, 3]}},
              "gate": {"enabled": True, "up": True, "auth": _XSS},
              "service_list": [{"name": _XSS, "label": _XSS, "port": 1, "state": _XSS}],
              "metrics": {"tool_calls": 3, "tool_errors": 1, "top_tools": [{"tool": _XSS, "calls": 3, "errors": 1}]},
              "events": [{"t": 1, "level": "warn", "text": _XSS}], "host": {"name": _XSS}}
    out = _node("__obdash.renderStatus(D); __obdash.renderStatus(D);\n"
                "process.stdout.write(JSON.stringify({model: document.getElementById('model-id').textContent,"
                " svc: document.getElementById('services').children.length,"
                " svcText: document.getElementById('services').textContent,"
                " tools: document.getElementById('tools').textContent,"
                " ev: document.getElementById('events').textContent}));", tmp_path, status)
    o = json.loads(out)
    assert o["model"] == _XSS                     # verbatim TEXT, through textContent
    assert o["svc"] == 1, "a second render updates in place, it does not append"
    assert _XSS in o["svcText"] and "n/a" in o["svcText"]   # an unknown state is not trusted as a class
    assert _XSS in o["tools"] and _XSS in o["ev"]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_node_fields_unified_discrete_none_and_missing(tmp_path):
    nodes = {
        "gb10": {"name": "spark-2", "role": "peer", "rank": 1, "state": "up", "hostname": "spark-9f26",
                 "gpu": {"name": "NVIDIA GB10", "count": 1, "unified": True, "mem_used_mib": 112640,
                         "mem_total_mib": 124928, "mem_pct": 90.2, "util_pct": 95, "temp_c": 61, "power_w": 31.3}},
        "rtx": {"name": "beast", "role": "head", "state": "up",
                "gpu": {"name": "NVIDIA GeForce RTX 5090", "count": 1, "unified": False, "mem_used_mib": 27497,
                        "mem_total_mib": 32607, "mem_pct": 84.3, "util_pct": 0, "temp_c": None, "power_w": None}},
        "none": {"name": "laptop", "role": "head", "state": "up", "gpu": None, "ram": None, "cpu": None},
        "lost": {"name": "spark-2", "role": "peer", "state": "unreachable", "error": "ssh failed", "last_ok_age_s": 47},
        "na": {"name": "spark-1", "state": "up", "gpu": {"name": "NVIDIA GB10", "unified": True,
                                                        "mem_used_mib": None, "mem_total_mib": None}},
    }
    out = _node("const o = {}; for (const k in D) o[k] = __obdash.nodeFields(D[k]);"
                "process.stdout.write(JSON.stringify(o));", tmp_path, nodes)
    f = json.loads(out)
    assert f["gb10"]["memLabel"] == "unified memory" and "110.0 / 122 GiB" in f["gb10"]["mem"]
    assert f["gb10"]["sub"] == "peer · rank 1 · spark-9f26" and f["gb10"]["power"] == "31 W"
    assert f["rtx"]["memLabel"] == "VRAM" and f["rtx"]["mem"] == "26.9 / 32 GiB" and "unified" not in f["rtx"]["mem"]
    assert f["rtx"]["util"] == "0%" and f["rtx"]["temp"] == "—" and f["rtx"]["power"] == "—"
    assert f["none"]["gpu"] == "no GPU detected" and f["none"]["mem"] == "—" and f["none"]["cpu"] == "—"
    assert f["lost"]["state"] == "unreachable" and "ssh failed" in f["lost"]["error"] and "47s ago" in f["lost"]["error"]
    assert f["na"]["mem"] == "not reported"
