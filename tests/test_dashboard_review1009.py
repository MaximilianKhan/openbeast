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


