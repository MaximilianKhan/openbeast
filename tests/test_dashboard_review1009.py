#!/usr/bin/env python3
"""Dashboard extension: the 2026-10-09 review fixes (netsec S5, S13; ops F6, F15).

Everything here builds its own case: `_get` and nvidia-smi are stubbed, the
HTTP server binds an ephemeral loopback port, and no stack is touched. The
/api/slot SHAPE is pinned by tests/test_beast_slot.py and is not re-tested.

Run: python3 -m pytest tests/test_dashboard_review1009.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(REPO, "extensions", "dashboard", "dashboard.py")
_ENV = ("OPENBEAST_PROBE_HOST", "OPENBEAST_INFERENCE_URL", "OPENBEAST_INFERENCE_BACKEND",
        "OPENBEAST_INFERENCE_SLOTS", "OPENBEAST_API_KEY", "EDGE_GATE",
        "OPENBEAST_EDGE_ALLOW_ANON", "OPENBEAST_INSTINCT", "OPENBEAST_INSTINCT_PORT")


def load(monkeypatch, **env):
    """A private copy of the module, imported under a controlled environment
    (its settings are read at import, like the real process reads them)."""
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("dashboard_review1009", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._kv_unified = lambda: True          # never read the real .run/
    return mod


_PROPS = {"total_slots": 1, "default_generation_settings": {"n_ctx": 4096}}
_ANSWERS = {"/health": (200, '{"status":"ok"}'), "/v1/models": (200, json.dumps({"data": [{"id": "m"}]})),
            "/props": (200, json.dumps(_PROPS)), "/slots": (200, "[]"), "/metrics": (501, ""),
            "/api/version": (200, '{"version":"1"}'), "/": (200, "")}


def recorder(seen):
    def get(url, timeout=2, auth=False):
        seen.append(url)
        for suffix, ans in _ANSWERS.items():
            if url.endswith(suffix):
                return ans
        return None, ""
    return get


# ───────────────────────── S5: one /health, cached, bounded ─────────────────────────

@pytest.mark.parametrize("backend", ["llama", "vllm", "tensorfold"])
def test_one_slot_document_probes_health_once(monkeypatch, backend):
    d = load(monkeypatch, OPENBEAST_INFERENCE_BACKEND=backend, OPENBEAST_INFERENCE_URL="http://10.9.9.9:8000")
    seen = []
    d._get = recorder(seen)
    out = d.slot_status()
    assert [u for u in seen if u == "http://10.9.9.9:8000/health"] == ["http://10.9.9.9:8000/health"], seen
    # the one answer still feeds both places that report it
    assert out["healthy"] is True and out["services"]["model"] is True


def test_status_document_probes_health_once(monkeypatch):
    d = load(monkeypatch)
    seen = []
    d._get = recorder(seen)
    d.gpu_status = lambda: None             # never the real nvidia-smi
    out = d.status()
    assert sum(u.endswith(":8080/health") for u in seen) == 1, seen
    assert out["model"]["healthy"] is True and out["services"]["model"] is True


def test_services_status_alone_still_probes_health(monkeypatch):
    # the control: with no prefetched answer it must ask, not report a default
    d = load(monkeypatch)
    seen = []
    d._get = recorder(seen)
    assert d.services_status()["model"] is True
    assert sum(u.endswith(":8080/health") for u in seen) == 1
    d._get = lambda url, timeout=2, auth=False: (None, "")
    assert d.services_status()["model"] is False


def test_cached_recomputes_once_per_ttl_however_many_callers(monkeypatch):
    d = load(monkeypatch)
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.05)
        return len(calls)
    c = d._Cached(slow, ttl=0.5)
    got = []
    ts = [threading.Thread(target=lambda: got.append(c.get())) for _ in range(40)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert calls == [1] and got == [1] * 40
    time.sleep(0.55)
    assert c.get() == 2, "an expired answer must be gathered again"


class Live:
    """The dashboard's own server on 127.0.0.1:<ephemeral>."""

    def __init__(self, d, **kw):
        self.srv = d.BoundedServer(("127.0.0.1", 0), d.Handler, **kw)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.t = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.t.start()

    def get(self, path, timeout=5):
        try:
            with urllib.request.urlopen(self.url + path, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.t.join(5)


def test_api_slot_is_served_from_the_cache(monkeypatch):
    d = load(monkeypatch)
    seen = []
    d._get = recorder(seen)
    live = Live(d)
    try:
        bodies = [live.get("/api/slot") for _ in range(25)]
        assert {st for st, _, _ in bodies} == {200}
        one = len(seen)
        assert 0 < one <= 8, seen            # ONE gather, not 25 x 9 upstream requests
        doc = json.loads(bodies[-1][2])
        assert doc == d.slot_status(), "the cached body is the contract's answer, unchanged"
        # control: once the answer has expired, the next caller pays for a fresh one
        n = len(seen)
        d._slot_cached._at = None
        assert live.get("/api/slot")[0] == 200 and len(seen) == n + one
    finally:
        live.close()


def test_handler_threads_are_capped_and_given_back(monkeypatch):
    d = load(monkeypatch)
    gate, entered = threading.Event(), threading.Event()

    def stuck():
        entered.set()
        assert gate.wait(10)
        return {"ok": True}
    d._slot_cached = d._Cached(stuck)
    before = threading.active_count()
    live = Live(d, max_handlers=2)
    held = []
    try:
        ts = [threading.Thread(target=lambda: held.append(live.get("/api/slot", timeout=15))) for _ in range(2)]
        for t in ts:
            t.start()
        assert entered.wait(5)
        deadline = time.time() + 5
        while threading.active_count() < before + 5 and time.time() < deadline:
            time.sleep(0.01)                 # both handler threads are in
        flood = [live.get("/api/slot") for _ in range(10)]
        assert {st for st, _, _ in flood} == {503}, flood
        assert flood[0][1].get("Retry-After") == "1"
        assert threading.active_count() <= before + 5, "a refused connection must not cost a thread"
        gate.set()
        for t in ts:
            t.join(10)
        assert [st for st, _, _ in held] == [200, 200]
        # the units came back: the server answers again
        deadline = time.time() + 5
        while (st := live.get("/api/slot")[0]) != 200 and time.time() < deadline:
            time.sleep(0.02)
        assert st == 200
    finally:
        gate.set()
        live.close()


# ───────────────────────── F6: probes follow OPENBEAST_PROBE_HOST ─────────────────────────

@pytest.mark.parametrize("env,host", [({}, "127.0.0.1"),
                                      ({"OPENBEAST_PROBE_HOST": "192.168.1.50"}, "192.168.1.50"),
                                      ({"OPENBEAST_PROBE_HOST": "[::1]"}, "[::1]"),
                                      ({"OPENBEAST_PROBE_HOST": "  "}, "127.0.0.1")])
def test_probes_dial_the_probe_host(monkeypatch, env, host):
    d = load(monkeypatch, OPENBEAST_INSTINCT="true", **env)
    seen = []
    d._get = recorder(seen)
    d.slot_status()
    d.tool_metrics()
    want = {f"http://{host}:8080/health", f"http://{host}:8080/v1/models", f"http://{host}:8080/props",
            f"http://{host}:8080/slots", f"http://{host}:8080/metrics", f"http://{host}:3001/health",
            f"http://{host}:3000/api/version", f"http://{host}:8888/", f"http://{host}:3001/metrics",
            # beast-instinct binds loopback whatever BIND_HOST says
            "http://127.0.0.1:8094/health"}
    assert set(seen) == want


def test_an_explicit_inference_url_still_wins(monkeypatch):
    d = load(monkeypatch, OPENBEAST_PROBE_HOST="192.168.1.50", OPENBEAST_INFERENCE_URL="http://10.0.0.5:8000/")
    assert d._INFER == "http://10.0.0.5:8000"


# ───────────────────────── F15: multi-GPU nvidia-smi output ─────────────────────────

_ONE = "NVIDIA GeForce RTX 5090, 21000, 32607, 97, 61\n"
_TWO = "NVIDIA GeForce RTX 3090 Ti, 20000, 24564, 90, 61\nNVIDIA GeForce RTX 3090 Ti, 4564, 24564, 10, 70\n"


def _smi(monkeypatch, d, out):
    def run(cmd, **kw):
        assert cmd[0] == "nvidia-smi"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    monkeypatch.setattr(d.subprocess, "run", run)


def test_single_gpu_answer_is_unchanged(monkeypatch):
    d = load(monkeypatch)
    _smi(monkeypatch, d, _ONE)
    assert d.gpu_status() == {"name": "NVIDIA GeForce RTX 5090", "used_mib": 21000, "total_mib": 32607,
                              "free_mib": 11607, "util_pct": 97, "temp_c": 61, "used_pct": 64}


def test_two_gpus_are_one_gpu_object_not_none(monkeypatch):
    d = load(monkeypatch)
    _smi(monkeypatch, d, _TWO)
    assert d.gpu_status() == {"name": "2x NVIDIA GeForce RTX 3090 Ti", "used_mib": 24564, "total_mib": 49128,
                              "free_mib": 24564, "util_pct": 90, "temp_c": 70, "used_pct": 50}
    _smi(monkeypatch, d, "RTX 5090, 1, 100, 2, 30\nRTX 3090, 1, 100, 3, 40\n")
    assert d.gpu_status()["name"] == "RTX 5090 + RTX 3090"


@pytest.mark.parametrize("out", ["", "\n", "garbage\n", "a, b, c, d, e\n"])
def test_unparseable_nvidia_smi_is_still_no_gpu(monkeypatch, out):
    d = load(monkeypatch)
    _smi(monkeypatch, d, out)
    assert d.gpu_status() is None


# ───────────────────────── S13: upstream strings never reach innerHTML raw ─────────────────────────

def _script(d):
    return re.search(r"<script>(.*)</script>", d.PAGE, re.S).group(1)


def test_every_interpolation_is_escaped_or_local(monkeypatch):
    """Static pin: an interpolation is either wrapped in esc()/num(), a
    two-literal ternary, or one of the fragments built just above it."""
    d = load(monkeypatch)
    exprs = re.findall(r"\$\{([^}]*)\}", _script(d))
    assert len(exprs) >= 15
    ok = re.compile(r"""^(?:esc\(.*\)|num\([\w.]+\)|\(num\([\w.]+\)/1024\)\.toFixed\(\d\)
                         |[\w.]+\?'[^'<]*':'[^'<]*'|gpu|svc)$""", re.X)
    bad = [e for e in exprs if not ok.match(e)]
    assert not bad, bad
    # the control: the pre-fix spellings are exactly what this refuses
    for raw in ("m.alias||m.serve_script||'—'", "g.name", "k", "g.used_pct"):
        assert not ok.match(raw)


_XSS = "<img src=x onerror=alert(1)>"
_HARNESS = r"""
const els = {grid: {innerHTML: ''}, ts: {textContent: ''}};
globalThis.document = {getElementById: id => els[id]};
globalThis.setInterval = () => 0;
globalThis.fetch = async () => ({json: async () => JSON.parse(process.env.STATUS)});
%s
setTimeout(() => process.stdout.write(els.grid.innerHTML), 50);
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_a_hostile_model_id_renders_as_text(monkeypatch, tmp_path):
    d = load(monkeypatch)
    status = {"gpu": {"name": _XSS, "used_mib": 1024, "total_mib": 2048, "free_mib": 1024,
                      "util_pct": _XSS, "temp_c": 50, "used_pct": "1 onmouseover=alert(1)"},
              "model": {"healthy": True, "alias": _XSS, "serve_script": _XSS},
              "services": {_XSS: True, "tools": False},
              "metrics": {"tool_calls": _XSS, "tool_errors": 0}}
    js = tmp_path / "page.js"
    js.write_text(_HARNESS % _script(d))
    r = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30,
                       env={"PATH": os.environ["PATH"], "STATUS": json.dumps(status)})
    assert r.returncode == 0, r.stderr
    html = r.stdout
    assert "<h2>Model</h2>" in html, html                 # the page did render
    assert "<img" not in html and "onmouseover" not in html, html
    assert "&#60;img src=x onerror=alert(1)&#62;" in html
    # the control: honest values still show
    status["model"]["alias"] = "qwen38-27b"
    r = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30,
                       env={"PATH": os.environ["PATH"], "STATUS": json.dumps(status)})
    assert ">qwen38-27b</div>" in r.stdout
