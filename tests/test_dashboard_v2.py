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
        "DASHBOARD_DISK_PATH", "OPENBEAST_DASHBOARD_KWH_PRICE", "DASHBOARD_KWH_PRICE",
        "OPENBEAST_DASHBOARD_CURRENCY", "DASHBOARD_CURRENCY")


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
                                             "ram": None, "disk": None, "uptime_s": None, "fabric": [],
                                             "power": None}
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


class FakePopen:
    """subprocess.Popen for _run_node: records the call, answers canned."""
    seen = {}

    def __init__(self, cmd, outcome="ok", answer="", rc=0, **kw):
        if outcome == "nossh":
            raise FileNotFoundError("ssh")
        FakePopen.seen.update(cmd=cmd, kw=kw)
        self.pid, self.returncode, self._out, self._outcome = 999999, rc, answer, outcome

    def communicate(self, input=None, timeout=None):
        FakePopen.seen.update(input=input, timeout=timeout)
        if self._outcome == "timeout" and input is not None:
            raise subprocess.TimeoutExpired("ssh", timeout)
        return self._out, "secret-ish"


def _popen(monkeypatch, d, **kw):
    killed = []
    monkeypatch.setattr(d.subprocess, "Popen", lambda cmd, **k: FakePopen(cmd, **kw, **k))
    monkeypatch.setattr(d.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    return killed


def test_peer_ssh_is_batch_mode_capped_and_fixed(monkeypatch):
    d = load(monkeypatch)
    d.NODE_SCRIPT = d.node_script("~/m")
    _popen(monkeypatch, d, answer=GB10)
    out, err = d._run_node("max@10.100.216.2")
    assert err is None and out == GB10
    seen = FakePopen.seen
    cmd = seen["cmd"]
    assert cmd[0] == "ssh" and "BatchMode=yes" in cmd and "ConnectTimeout=3" in cmd
    assert cmd[-3:] == ["--", "max@10.100.216.2", "sh -s"]
    assert seen["input"] == d.NODE_SCRIPT and seen["timeout"] == d.PEER_TIMEOUT_S
    # its own process group, so a timeout can kill the whole tree
    assert seen["kw"]["start_new_session"] is True


@pytest.mark.parametrize("outcome,want", [
    ("timeout", "no answer within"), ("rc255", "ssh failed"), ("empty", "empty answer"), ("nossh", "ssh not available")])
def test_an_unreachable_peer_reads_unreachable(monkeypatch, outcome, want):
    d = load(monkeypatch)
    killed = _popen(monkeypatch, d, outcome=outcome, rc=255 if outcome == "rc255" else 0)
    s = d.NodeSampler("spark-2", "spark-2", "peer", 1)
    s.sample()
    snap = s.snapshot()
    assert snap["state"] == "unreachable" and want in snap["error"] and "secret" not in snap["error"]
    assert snap["gpu"] is None and snap["fabric"] == []
    assert bool(killed) == (outcome == "timeout"), "a timeout kills the process group, nothing else does"
    # the failed sample is a gap on the history's time axis
    assert snap["history"]["util"] == [None] and len(snap["history"]["t"]) == 1


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
    # the failed first sample stays on the time axis as a gap
    assert snap["history"]["util"] == [None, 95, 95]
    assert len(snap["history"]["t"]) == len(snap["history"]["power"]) == 3
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
        # the page never runs a probe, and /api/status (an HTTP gather of the stack, all
        # stubbed here) takes its gpu block from the head sampler: no subprocess either
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
    d._get = _canned({"/health": (200, '{"status":"ok"}'), "/slots": (200, "[]"),
                      "/metrics": (200, "llamacpp:tokens_predicted_total 1000\nllamacpp:prompt_tokens_total 50\n")})
    d.inference_unit = lambda units=None: {"name": "llama-server", "active": True, "uptime_s": 60}
    s = d.ServeSampler()
    s.sample(now=1000.0)
    d._get = _canned({"/health": (200, '{"status":"ok"}'), "/slots": (200, "[]"),
                      "/metrics": (200, "llamacpp:tokens_predicted_total 1600\nllamacpp:prompt_tokens_total 150\n")})
    gate["/gate/metrics"] = (200, metrics.replace("} 40", "} 70"))
    s.sample(now=1010.0)
    snap = s.snapshot()
    g = snap["gate"]
    assert g == {"enabled": True, "up": True, "auth": "devices", "devices": 2, "active_devices": 1,
                 "denied_total": 3, "denied_identity": 3, "denied_by_reason": {"no_key": 3},
                 "requests_total": 72, "errors_total": 2, "req_per_min": 180.0}
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
    assert f["gb10"]["sub"] == "peer · rank 1 · spark-9f26" and f["gb10"]["power"] == "31.3 W"
    assert f["rtx"]["memLabel"] == "VRAM" and f["rtx"]["mem"] == "26.9 / 32 GiB" and "unified" not in f["rtx"]["mem"]
    assert f["rtx"]["util"] == "0%" and f["rtx"]["temp"] == "—" and f["rtx"]["power"] == "—"
    assert f["none"]["gpu"] == "no GPU detected" and f["none"]["mem"] == "—" and f["none"]["cpu"] == "—"
    assert f["lost"]["state"] == "unreachable" and "ssh failed" in f["lost"]["error"] and "47s ago" in f["lost"]["error"]
    assert f["na"]["mem"] == "not reported"


# ═════════════════════════ review 2026-11-22: power, and the review's fixes ═════════════════════════

# Real probe lines, read 2026-11-22 (read-only) from the three hosts.
GB10_LONG = "gpu|NVIDIA GB10, [N/A], [N/A], 94, 62, 31.98, 30.64, 31.98, [N/A]\nnrg|18676165726|1|383142.100\n"
RTX_LONG = ("gpu|NVIDIA GeForce RTX 5090, 27420, 32607, 0, 42, 16.60, 16.60, 16.60, 600.00\n"
            "nrg|6372307571|1|24058.633\npwr|amdgpu|power1|PPT|input|50000|\n")


def _view(d, text):
    raw = d.parse_node(text)
    return raw, d.node_view(raw)


def test_sensor_parsing_gb10_is_the_gpu_rail_only(monkeypatch):
    d = load(monkeypatch)
    raw, v = _view(d, GB10_LONG)
    g, p = v["gpu"], v["power"]
    assert g["power_w"] == 32.0 and g["power_instant_w"] == 30.6 and g["power_avg_w"] == 32.0
    assert g["power_limit_w"] is None                       # [N/A] on GB10: no limit, never 0
    assert raw["energy"] == {"mj": 18676165726, "gpus": 1, "mono": 383142.1}
    assert p["label"] == "GPU power" and p["measured"] == "gpu" and p["total_w"] == 30.6
    assert p["gpu_source"] == "power.draw.instant" and "GPU rail only" in p["gpu_scope"]
    assert "system" not in p["label"]


def test_sensor_parsing_5090_board_plus_cpu_package(monkeypatch):
    d = load(monkeypatch)
    raw, v = _view(d, RTX_LONG)
    p = v["power"]
    assert v["gpu"]["power_limit_w"] == 600.0 and p["gpu_limit_w"] == 600.0
    cpu = [r for r in p["rails"] if r["kind"] == "cpu"][0]
    # the APU SMU's whole watts filed in the mW field: 50000 "uW" is 50 W, and says so
    assert cpu["label"] == "CPU package (PPT)" and cpu["w"] == 50.0 and "mW field" in cpu["note"]
    assert p["label"] == "GPU + CPU package" and p["measured"] == "partial" and p["total_w"] == 66.6
    assert "board power" in p["gpu_scope"]


def test_sensor_parsing_old_driver_na_and_junk(monkeypatch):
    d = load(monkeypatch)
    # an older driver answers the short query: power.draw only, no instant/average/limit
    _, v = _view(d, "gpu|NVIDIA GeForce RTX 3090, 100, 24576, 5, 40, 30.5\n")
    assert v["gpu"]["power_instant_w"] is None and v["power"]["gpu_source"] == "power.draw"
    assert v["power"]["total_w"] == 30.5
    # every power field [N/A]: no power at all, never a 0
    _, v = _view(d, "gpu|NVIDIA GB10, [N/A], [N/A], 1, 40, [N/A], [N/A], [N/A], [N/A]\n")
    assert v["gpu"]["power_w"] is None and v["power"] is None
    # a broken driver: nvidia-smi present, no rows
    _, v = _view(d, "gpuerr|9\n")
    assert v["gpu"] is None and v["gpu_error"] == 9
    # NaN / inf / garbage never become numbers
    raw, v = _view(d, "gpu|X, 1, 2, nan, inf, nan, inf, -inf, nan\nnrg|nan|1|5\nnrg|-5|1|5\nload|nan inf -inf\n"
                      "pwr|amdgpu|power1|PPT|input|nan|\npwr|x\n")
    assert v["gpu"]["power_w"] is None and v["gpu"]["util_pct"] is None and "energy" not in raw
    assert raw["load"] == [None, None, None] and v["power"] is None
    json.dumps(v, allow_nan=False)


def test_hwmon_rails_are_named_by_what_they_are(monkeypatch):
    d = load(monkeypatch)
    r = d._rail("power_meter|power1||average|185000000|".split("|"))
    assert r["kind"] == "system" and r["w"] == 185.0
    # a kernel that reports the APU in real microwatts: no correction
    r = d._rail("amdgpu|power1|PPT|input|42000000|".split("|"))
    assert r["kind"] == "cpu" and r["w"] == 42.0 and r["note"] is None
    # a discrete AMD card (it has a cap) is a GPU board, never the CPU
    assert d._rail("amdgpu|power1|PPT|average|42000000|cap".split("|"))["kind"] == "gpu"
    assert d._rail("ina3221|power2|VDD_CPU|input|3000000|".split("|"))["label"] == "ina3221 VDD_CPU"
    # with a system meter, the node figure IS the system figure
    _, v = _view(d, "gpu|NVIDIA GeForce RTX 5090, 1, 2, 0, 40, 20, 20, 20, 600\n"
                    "pwr|power_meter|power1||average|185000000|\n")
    assert v["power"]["label"] == "system power" and v["power"]["total_w"] == 185.0


def _np(d, gap=None):
    return d.NodePower() if gap is None else d.NodePower(gap=gap)


def _feed(d, np_, wall, mono, gpu_w=None, mj=None, emono=None, rails="", gpus=1):
    text = ""
    if gpu_w is not None:
        text += f"gpu|NVIDIA GeForce RTX 5090, 1, 2, 0, 40, {gpu_w}\n"
    if mj is not None:
        text += f"nrg|{mj}|{gpus}|{emono if emono is not None else mono}\n"
    text += rails
    raw = d.parse_node(text)
    return np_.ingest(wall, mono, raw, d.node_view(raw))


def test_nvml_energy_gives_the_exact_interval_mean_and_a_reset_falls_back(monkeypatch):
    d = load(monkeypatch)
    np_ = _np(d)
    T = 1_800_000_000
    assert _feed(d, np_, T, 100.0, gpu_w=17, mj=1_000_000) is None          # first sample: no interval
    # 174,400 mJ over 5 s = 34.88 W, though both 1 s readings said 17 W (the bursty-load case)
    assert _feed(d, np_, T + 5, 105.0, gpu_w=17, mj=1_174_400) == pytest.approx(34.88)
    assert np_.source == "nvml-energy"
    # the counter went backwards (driver reload): the trapezoid of the readings instead
    assert _feed(d, np_, T + 10, 110.0, gpu_w=20, mj=5) == pytest.approx(18.5)
    assert np_.source == "power.draw"
    # the node's clock disagrees wildly with ours (a reboot): trapezoid again
    _feed(d, np_, T + 15, 115.0, gpu_w=20, mj=105, emono=5.0)
    assert _feed(d, np_, T + 20, 120.0, gpu_w=30, mj=500_000, emono=900.0) == pytest.approx(25.0)


def test_time_weighted_averages_with_gaps_never_count_zeros(monkeypatch):
    d = load(monkeypatch)
    np_ = _np(d)
    T = 1_800_000_000 - (1_800_000_000 % 60)
    mono, mj = 0.0, 0
    # 10 min at 100 W, then 10 min with NO samples (unreachable), then 10 min at 300 W
    for i in range(121):
        _feed(d, np_, T + 5 * i, mono + 5 * i, gpu_w=100, mj=mj + 500_000 * i)
    base_t, base_m, base_e = T + 1200, 1200.0, mj + 500_000 * 120 + 60_000_000
    for i in range(121):
        _feed(d, np_, base_t + 5 * i, base_m + 5 * i, gpu_w=300, mj=base_e + 1_500_000 * i)
    now = base_t + 600
    st = np_.stats(now, 1800)
    # 600 s at 100 W and 600 s at 300 W: 200 W time-weighted; the gap is not 600 s of 0 W
    assert st["avg_w"] == 200.0 and st["coverage"] == pytest.approx(1200 / 1800, abs=0.01)
    assert st["energy_wh"] == pytest.approx((600 * 100 + 600 * 300) / 3600, rel=1e-3)
    assert st["min_w"] == 100.0 and st["max_w"] == 300.0
    # the window that holds only the gap and one edge sample reads collecting, not 0
    gap_only = np_.stats(T + 600 + 300, 290)
    assert gap_only["avg_w"] is None and gap_only["coverage"] < d.POWER_MIN_COVERAGE


def test_windows_peak_and_energy(monkeypatch):
    d = load(monkeypatch)
    np_ = _np(d)
    T = 1_800_000_000 - (1_800_000_000 % 60)
    e = 0
    watts = [50] * 60 + [250] * 12 + [50] * 108       # 15 min; a 1 min burst at 250 W
    for i, w in enumerate(watts):
        e += w * 5 * 1000
        _feed(d, np_, T + 5 * (i + 1), 5.0 * (i + 1), gpu_w=w, mj=e)
    now = T + 5 * len(watts)
    st = {k: np_.stats(now, w) for k, w in d.POWER_WINDOWS}
    assert st["1m"]["avg_w"] == 50.0 and st["1m"]["max_w"] == 50.0
    assert st["15m"]["max_w"] == 250.0 and st["15m"]["min_w"] == 50.0
    assert st["15m"]["energy_wh"] == pytest.approx((179 * 5 * 50 + 12 * 5 * 200) / 3600, rel=1e-3)
    assert st["15m"]["avg_w"] == pytest.approx(st["15m"]["energy_wh"] * 3600 / st["15m"]["covered_s"], abs=0.1)
    # the 24 h window is the 1 min tier: same energy, its coverage a small part of a day
    assert st["24h"]["energy_wh"] == pytest.approx(st["15m"]["energy_wh"], rel=1e-3)
    assert st["24h"]["avg_w"] is None and st["24h"]["coverage"] < 0.02
    # an interval that straddles slot boundaries is split exactly
    tier = d._Tier(5, 3600)
    tier.add(T + 3, T + 8, 100.0, 100.0)
    assert tier.slots[T][0] == pytest.approx(2) and tier.slots[T + 5][1] == pytest.approx(300)


def test_trend_rising_falling_steady_and_collecting(monkeypatch):
    d = load(monkeypatch)
    def mk(a, b, c=None, c5=300, cb=900, c1=3600):
        return {"5m": {"avg_w": a, "covered_s": c5, "coverage": c5 / 300},
                "15m": {"avg_w": b, "covered_s": cb, "coverage": cb / 900},
                "1h": {"avg_w": c, "covered_s": c1, "coverage": c1 / 3600}}
    assert d.trend(mk(110, 100))["dir"] == "rising" and d.trend(mk(110, 100))["pct"] == 10.0
    assert d.trend(mk(90, 100))["dir"] == "falling"
    assert d.trend(mk(104, 100))["dir"] == "steady"              # inside the ±5 % deadband
    assert d.trend(mk(None, 100))["dir"] is None
    assert d.trend(mk(120, None, 100))["vs"] == "1h"             # no 15 min yet: the hour
    # a cold start: ~90 s in both windows is no comparison, so "collecting", not "steady 0 %"
    assert d.trend(mk(100, 100, None, c5=90, cb=90, c1=90))["dir"] is None
    # the 5 min window full, but the baseline barely more: still collecting
    assert d.trend(mk(100, 100, None, c5=300, cb=330, c1=330))["dir"] is None
    assert d.trend(mk(100, 100, None, c5=300, cb=450))["dir"] == "steady"


def _sampler_with(d, name, watts, T, outage=()):
    s = d.NodeSampler(name, None if name == "a" else name)
    e = 0
    for i, w in enumerate(watts):
        e += w * 5 * 1000
        if i in outage:
            continue
        raw = d.parse_node(f"gpu|NVIDIA GB10, [N/A], [N/A], 1, 40, {w}, {w}, {w}, [N/A]\nnrg|{e}|1|{5.0 * (i + 1)}\n")
        s.power.ingest(T + 5 * (i + 1), 5.0 * (i + 1), raw, d.node_view(raw))
        s.view, s.state = d.node_view(raw), "up"
    return s


def test_cluster_total_is_the_sum_and_says_n_of_m(monkeypatch):
    d = load(monkeypatch)
    T = 1_800_000_000 - (1_800_000_000 % 60)
    a = _sampler_with(d, "a", [30] * 180, T)
    b = _sampler_with(d, "b", [40] * 180, T, outage=range(100, 120))
    now = T + 900
    ps = d.power_summary([a.power_row(), b.power_row()], now=now)
    c = ps["cluster"]
    assert c["now_w"] == 70.0 and c["nodes_measured"] == 2 and c["label"] == "GPU power"
    assert c["windows"]["15m"]["avg_w"] == 70.0                    # each node over its own covered time
    assert c["windows"]["15m"]["coverage"] < 1                     # b's outage shows
    assert c["windows"]["15m"]["max_w"] == 70.0                    # peak only of slots both measured
    assert c["trend"]["dir"] == "steady" and c["measured"] == ["a", "b"]
    b.state = "unreachable"
    ps = d.power_summary([a.power_row(), b.power_row()], now=now)
    assert ps["cluster"]["now_w"] == 30.0 and ps["cluster"]["nodes_measured"] == 1
    # a partial sum: no cluster trend over a node set the now figure does not have
    assert ps["cluster"]["measured"] == ["a"] and ps["cluster"]["trend"]["partial"] is True
    assert ps["cluster"]["trend"]["dir"] is None
    assert ps["nodes"][1]["now"] is None and ps["nodes"][1]["last"]["total_w"] == 40.0
    ch = d.power_chart([a.power_row(), b.power_row()], "15m", now=now)
    assert ch["step_s"] == 5 and len(ch["nodes"][0]["values"]) == 180
    assert any(v is None for v in ch["nodes"][1]["values"]) and ch["total"][50] == 70.0
    assert all(t is None for t, bv in zip(ch["total"], ch["nodes"][1]["values"]) if bv is None)
    assert d.power_chart([a.power_row()], "bogus", now=now)["window"] == "15m"


def test_power_persistence_round_trip_cap_and_corruption(monkeypatch, tmp_path):
    d = load(monkeypatch)
    T = 1_800_000_000 - (1_800_000_000 % 60)
    a = _sampler_with(d, "a", [30] * 180, T)
    path = str(tmp_path / "dashboard-power.json")
    n = d.save_power([a], path, now=T + 900)
    assert n > 0 and os.stat(path).st_mode & 0o777 == 0o600
    assert not [f for f in os.listdir(tmp_path) if ".tmp." in f]          # the temp file was renamed
    b = d.NodeSampler("a")
    assert d.load_power([b], path, now=T + 900) == 1
    assert b.power.stats(T + 900, 900) == a.power.stats(T + 900, 900)
    # past a tier's span on load: dropped
    c = d.NodeSampler("a")
    d.load_power([c], path, now=T + 900 + 2 * 86400)
    assert c.power.stats(T + 900 + 2 * 86400, 86400)["covered_s"] == 0
    # the size cap: the 5 s tier goes first, then nothing is written
    monkeypatch.setattr(d, "POWER_FILE_MAX", 6000)
    assert 0 < d.save_power([a], path, now=T + 900) <= 6000
    assert json.load(open(path))["nodes"]["a"]["fine"] == []
    monkeypatch.setattr(d, "POWER_FILE_MAX", 100)
    before = open(path).read()
    assert d.save_power([a], path, now=T + 900) == 0 and open(path).read() == before
    # corrupt, wrong layout, or too big: restores nothing, raises nothing
    for junk in ("{not json", '{"v": 9}', '[]', '{"v":1,"fine_s":5,"coarse_s":60,"nodes":{"a":{"fine":[["x"]],"coarse":7}}}'):
        open(path, "w").write(junk)
        e = d.NodeSampler("a")
        d.load_power([e], path, now=T + 900)
        assert e.power.stats(T + 900, 900)["covered_s"] == 0
    monkeypatch.setattr(d, "POWER_FILE_MAX", 4 << 20)
    open(path, "w").write(" " * (5 << 20))
    assert d.load_power([d.NodeSampler("a")], path, now=T) == 0


def test_jittered_wall_stamps_never_overcover_a_slot_and_survive_a_restore(monkeypatch, tmp_path):
    """review 2026-11-22: the counter's dt and our wall gap differ by ms, so
    intervals overlapped, slots summed past their step, and load dropped
    about half of them after every restart."""
    import random
    d = load(monkeypatch)
    rng = random.Random(7)
    T = 1_800_000_000 - (1_800_000_000 % 60)
    s = d.NodeSampler("a")
    e = 0
    for i in range(721):                       # 1 h at 40 W, 5 s cadence
        e += 40 * 5 * 1000
        mono = 5.0 * (i + 1)
        wall = T + mono + rng.uniform(0, 0.4)  # the probe-to-stamp latency
        raw = d.parse_node(f"gpu|NVIDIA GB10, [N/A], [N/A], 1, 40, 40, 40, 40, [N/A]\nnrg|{e}|1|{mono}\n")
        s.power.ingest(wall, mono, raw, d.node_view(raw))
    now = T + 5 * 722
    for tier in (s.power.fine, s.power.coarse):
        assert max(v[0] for v in tier.slots.values()) <= tier.step + 1e-9
    before = {k: s.power.stats(now, w) for k, w in d.POWER_WINDOWS}
    assert before["1h"]["covered_s"] > 3500 and before["1h"]["avg_w"] == 40.0
    path = str(tmp_path / "p.json")
    assert d.save_power([s], path, now=now) > 0
    r = d.NodeSampler("a")
    assert d.load_power([r], path, now=now) == 1
    after = {k: r.power.stats(now, w) for k, w in d.POWER_WINDOWS}
    for k in before:
        assert after[k]["covered_s"] == pytest.approx(before[k]["covered_s"], abs=0.5), k
        assert after[k]["energy_wh"] == pytest.approx(before[k]["energy_wh"], rel=1e-3), k
    # a file from 4205caef (slots a hair over their step) is clamped on load, not dropped
    doc = json.load(open(path))
    bumped = 0
    for row in doc["nodes"]["a"]["fine"]:
        if row[1] >= 5 - 1e-6:              # a full slot, written a hair over its step
            row[1], row[2], row[3], row[4] = 5.013, row[2] * 1.0026, 5.013, row[4] * 1.0026
            bumped += 1
    assert bumped > 300
    json.dump(doc, open(path, "w"))
    o = d.NodeSampler("a")
    d.load_power([o], path, now=now)
    assert o.power.stats(now, 3600)["covered_s"] == pytest.approx(before["1h"]["covered_s"], abs=1)
    assert o.power.stats(now, 3600)["avg_w"] == 40.0


def test_save_power_is_serialised(monkeypatch, tmp_path):
    d = load(monkeypatch)
    T = 1_800_000_000 - (1_800_000_000 % 60)
    a = _sampler_with(d, "a", [30] * 12, T)
    path = str(tmp_path / "p.json")
    with d._save_lock:                          # the saver thread mid-write
        assert d.save_power([a], path, now=T + 60, lock_timeout=0.05) == 0
    assert d.save_power([a], path, now=T + 60) > 0


def test_cost_shows_only_when_a_price_is_set(monkeypatch, tmp_path):
    d = load(monkeypatch, repo=tmp_path)
    T = 1_800_000_000 - (1_800_000_000 % 60)
    a = _sampler_with(d, "a", [100] * 180, T)
    assert d._kwh_price() == (None, None)
    assert d.power_summary([a.power_row()], now=T + 900)["cost"] is None
    for bad in ("abc", "-1", "0", "nan", "1e9"):
        monkeypatch.setenv("DASHBOARD_KWH_PRICE", bad)
        assert d._kwh_price() == (None, None), bad
    monkeypatch.delenv("DASHBOARD_KWH_PRICE")
    (tmp_path / "openbeast.conf").write_text('DASHBOARD_KWH_PRICE="0.30"\nDASHBOARD_CURRENCY=EUR\n')
    price, cur = d._kwh_price()
    assert (price, cur) == (0.3, "EUR")
    co = d.power_summary([a.power_row()], now=T + 900, price=price, currency=cur)["cost"]
    # 100 W for a day = 2.4 kWh = 0.72 at 0.30; a month of 30.44 days
    assert co["per_day"] == 0.72 and co["per_month"] == round(2.4 * 30.44 * 0.3, 2)
    assert co["basis_window"] == "1h" and co["window_24h_kwh"] == pytest.approx(0.025, abs=1e-3)
    assert "not wall power" in co["note"]


def test_api_power_and_cluster_power_and_slot_unchanged(monkeypatch):
    d = load(monkeypatch, **_SLOT_ENV["vllm"])
    d._get = _canned(_SLOT_ANS["vllm"])
    T = time.time()
    d._head = _sampler_with(d, "a", [30] * 60, T - 300)
    d._peers = []
    live = Live(d)
    try:
        st, hdr, body = live.req("GET", "/api/power?window=1h", "127.0.0.1")
        doc = json.loads(body)
        assert st == 200 and doc["window"] == "1h" and doc["step_s"] == 5 and hdr["Cache-Control"] == "no-store"
        assert json.loads(live.req("GET", "/api/power?window=../../etc", "127.0.0.1")[2])["window"] == "15m"
        assert live.req("GET", "/api/power", "evil.example")[0] == 400
        cl = json.loads(live.req("GET", "/api/cluster", "127.0.0.1")[2])
        assert cl["power"]["cluster"]["nodes"] == 1 and set(cl["power"]["nodes"][0]["windows"]) == {"1m", "5m", "15m", "1h", "24h"}
        # the contract does not move one byte with power samplers present
        assert live.req("GET", "/api/slot", "x.lan")[2].decode() == _SLOT_GOLDEN["vllm"]
    finally:
        live.close()


def test_api_status_gpu_comes_from_the_head_sampler_never_nvidia_smi(monkeypatch):
    d = load(monkeypatch)
    _no_upstreams(d, monkeypatch)
    d.gpu_status = lambda: (_ for _ in ()).throw(AssertionError("a request ran nvidia-smi"))
    head = d.NodeSampler("spark-1", None, "head", 0)
    head.sample(run=lambda t: (GB10 + "nrg|5|1|5\n", None))
    d._head = head
    t0 = time.monotonic()
    g = d.status()["gpu"]
    assert time.monotonic() - t0 < 3.5
    assert g["name"] == "NVIDIA GB10" and g["unified"] is True and g["util_pct"] == 95 and g["power_w"] == 31.3
    # the head's whole probe failing: no last-good reading served as current
    head.sample(run=lambda t: (None, "no answer within 6 s"))
    assert d.status()["gpu"] is None


def test_a_hung_nvidia_smi_is_killed_not_orphaned(monkeypatch, tmp_path):
    d = load(monkeypatch)
    marker = f"obdash-hang-{os.getpid()}"
    fake = tmp_path / "nvidia-smi"
    fake.write_text(f"#!/bin/sh\nexec sleep 30 # {marker}\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    d.NODE_SCRIPT = d.node_script(str(tmp_path))
    d.PEER_TIMEOUT_S = 1.5
    out, err = d._run_node(None)
    assert out is None and "no answer" in err
    time.sleep(0.3)
    left = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
    assert not left, "the hung nvidia-smi was left behind"
    # ...and the probe bounds nvidia-smi itself where coreutils timeout exists
    assert "timeout -k 1 3" in d.NODE_SCRIPT and "timeout -k 0.5 1" in d.NODE_SCRIPT and "gpuerr|" in d.NODE_SCRIPT


def test_active_devices_is_right_in_summer_time(monkeypatch, tmp_path):
    (tmp_path / ".run").mkdir()
    (tmp_path / ".run" / "clients-lastseen.json").write_text(json.dumps({"laptop": "2026-07-15T12:00:00Z"}))
    d = load(monkeypatch, repo=tmp_path)
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    try:
        import calendar
        assert d._active_devices(now=calendar.timegm((2026, 7, 15, 12, 1, 0, 0, 0, 0))) == 1
        assert d._active_devices(now=calendar.timegm((2026, 7, 15, 12, 20, 0, 0, 0, 0))) == 0
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_refused_no_identity_counts_only_identity_reasons(monkeypatch):
    d = load(monkeypatch, EDGE_GATE="true")
    metrics = ('openbeast_edge_denied_total{reason="no_key"} 1\nopenbeast_edge_denied_total{reason="fanout"} 1\n'
               'openbeast_edge_denied_total{reason="rate_limited"} 1\n')
    d._gate_get = lambda path: (200, metrics) if path == "/gate/metrics" else (200, "{}")
    d._get = lambda url, timeout=2, auth=False: (None, "")
    d.inference_unit = lambda units=None: {"name": None, "active": False, "uptime_s": None}
    s = d.ServeSampler()
    s.sample(now=1000.0)
    g = s.snapshot()["gate"]
    assert g["denied_identity"] == 1 and g["denied_total"] == 3
    assert g["denied_by_reason"] == {"fanout": 1, "no_key": 1, "rate_limited": 1}


def test_a_stalled_tensorfold_pair_is_down_everywhere(monkeypatch):
    d = load(monkeypatch, **_SLOT_ENV["tensorfold"])
    stalled = dict(_SLOT_ANS["tensorfold"])
    stalled["/health"] = (200, json.dumps({"ok": True, "iteration_s": 500}))
    d._get = _canned(stalled)
    d.gpu_status = lambda: None
    out = d.status()
    assert out["model"]["healthy"] is False and out["services"]["model"] is False
    assert {s["name"]: s["state"] for s in out["service_list"]}["model"] == "down"
    assert d.slot_status()["healthy"] is False and d.slot_status()["services"]["model"] is False


def test_llama_tok_s_is_live_during_a_generation(monkeypatch):
    d = load(monkeypatch)
    slots = lambda n: (200, json.dumps([{"id": 0, "is_processing": n > 0, "next_token": [{"n_decoded": n}]}]))
    assert d.llama_inflight_tokens(*slots(250)) == 250 and d.llama_inflight_tokens(None, "") is None
    assert d.llama_inflight_tokens(200, "garbage") is None
    d.inference_unit = lambda units=None: {"name": "llama-server", "active": True, "uptime_s": 1}
    ans = {"/health": (200, '{"status":"ok"}')}
    d._get = _canned(ans)
    s = d.ServeSampler()
    # the finished-request counter is flat for the whole generation; n_decoded rises 60/s;
    # the request finishes at 1800 tokens and the next one starts in the same step
    for i, (total, n) in enumerate([(1000, 0), (1000, 600), (1000, 1200), (2800, 0), (2800, 600)]):
        ans["/metrics"] = (200, f"llamacpp:tokens_predicted_total {total}\n")
        ans["/slots"] = slots(n)
        s.sample(now=1000.0 + 10 * i)
        if i:
            assert s.snapshot()["tok_s"] == 60.0, i         # live, and no spike at completion
    assert s.snapshot()["tok_s_source"] == "engine"
    # a 6-significant-digit counter widens its window
    assert d._counter_window(5e5) == 10.0 and d._counter_window(1.83431e6) == 20.0
    assert d._counter_window(2e7) == 120.0


def test_json_never_carries_nan(monkeypatch):
    d = load(monkeypatch)
    doc = {"a": float("nan"), "b": [1.0, float("inf")], "c": {"d": -float("inf")}}
    assert json.loads(d._json(doc)) == {"a": None, "b": [1.0, None], "c": {"d": None}}
    assert d._prom("x NaN\nx 2\n", "x") == [2.0]


def test_any_other_method_is_405_not_501(monkeypatch):
    d = load(monkeypatch)
    _no_upstreams(d, monkeypatch)
    live = Live(d)
    try:
        for m in ("TRACE", "PROPFIND", "BREW"):
            st, hdr, _ = live.req(m, "/api/status")
            assert st == 405 and hdr.get("Allow") == "GET", m
    finally:
        live.close()


def test_disk_pct_is_dfs_use_percent(monkeypatch):
    d = load(monkeypatch)
    _, v = _view(d, "disk|/dev/x 1000 500 400 56% /\n")
    assert v["disk"]["pct"] == round(100 * 500 / 900, 1)


# ───────────────────────── the page script: power, keyed, stale ─────────────────────────

@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_keyed_lists_survive_duplicate_keys(tmp_path):
    out = _node("const box = document.getElementById('box'); const counts = [];"
                "for (let i = 0; i < 5; i++) { __obdash.keyed(box, D, x => x.k, () => document.createElement('li'),"
                " (el, x) => { el.textContent = x.v; }); counts.push(box.children.length); }"
                "process.stdout.write(JSON.stringify({counts, text: box.textContent}));",
                tmp_path, [{"k": "spark-2", "v": "a"}, {"k": "spark-2", "v": "b"}, {"k": "x", "v": "c"}])
    o = json.loads(out)
    assert o["counts"] == [3, 3, 3, 3, 3] and o["text"] == "abc"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_health_chip_goes_unknown_when_the_server_stops_answering(tmp_path):
    out = _node("const st = {model: {healthy: true}, service_list: []};"
                "__obdash._setData(st, {nodes: []});"
                "__obdash._setLast(Date.now(), Date.now()); __obdash.renderHealth();"
                "const fresh = document.getElementById('health-chip').dataset.state;"
                "__obdash._setLast(Date.now() - 60000, Date.now() - 60000); __obdash.renderHealth();"
                "process.stdout.write(JSON.stringify({fresh, stale: document.getElementById('health-chip').dataset.state,"
                " text: document.getElementById('health-text').textContent,"
                " nodes: document.getElementById('nodes').dataset.stale}));", tmp_path, {})
    o = json.loads(out)
    assert o["fresh"] == "ok" and o["stale"] == "unknown" and "not answering" in o["text"] and o["nodes"] == "1"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_power_fields_label_what_is_measured(tmp_path, monkeypatch):
    d = load(monkeypatch)
    T = 1_800_000_000 - (1_800_000_000 % 60)
    a = _sampler_with(d, "spark-1", [31.98] * 180, T)
    b = _sampler_with(d, "spark-2", [38.0] * 180, T)
    ps = d.power_summary([a.power_row(), b.power_row()], now=T + 900)
    rtx = d.NodeSampler("beast")
    raw = d.parse_node(RTX_LONG)
    rtx.view, rtx.state = d.node_view(raw), "up"
    pr = d.power_summary([rtx.power_row()], now=T + 900)
    js = ("const o = {spark: D.ps.nodes.map(__obdash.powerNodeFields), rtx: __obdash.powerNodeFields(D.pr.nodes[0]),"
          " win: __obdash.windowFields('24h', D.ps.cluster.windows['24h']), trend: __obdash.trendText(D.ps.cluster.trend),"
          " foot: __obdash.powerFoot(D.ps), model: __obdash.chartModel(D.chart)};"
          "__obdash.renderPower(D.ps); __obdash.renderPower(D.ps);"
          "o.now = document.getElementById('pw-now').textContent; o.k = document.getElementById('pw-now-k').textContent;"
          "o.rows = document.getElementById('pw-nodes').children.length; o.chips = document.getElementById('pw-windows').children.length;"
          "process.stdout.write(JSON.stringify(o));")
    chart = d.power_chart([a.power_row(), b.power_row()], "15m", now=T + 900)
    o = json.loads(_node(js, tmp_path, {"ps": ps, "pr": pr, "chart": chart}))
    assert o["spark"][0]["label"] == "GPU power" and "GPU 32.0 W instant" in o["spark"][0]["rails"]
    assert o["spark"][0]["limitText"] == "" and "GPU rail only" in o["spark"][0]["scope"]
    assert "GPU + CPU package" in o["rtx"]["label"] and "CPU package (PPT) 50.0 W †" in o["rtx"]["rails"]
    assert "of the 600 W board limit" in o["rtx"]["limitText"]
    assert o["win"]["value"] == "collecting" and "kWh" in o["win"]["energy"]
    assert o["trend"]["dir"] == "steady" and "GB10" in o["foot"] and "never counted as zero" in o["foot"]
    assert [s["cls"] for s in o["model"]["series"]] == ["c0", "c1", "ln-total"] and o["model"]["top"] >= 70
    assert o["now"] == "70.0" and o["k"].startswith("cluster · GPU power") and o["rows"] == 2 and o["chips"] == 5
