#!/usr/bin/env python3
"""GATE_DEFAULT_MAX_TOKENS — beast-gate's default reply cap.

A /v1/chat/completions or /v1/completions body that sets neither max_tokens
nor max_completion_tokens (nor llama.cpp's n_predict) gets max_tokens = the
default; a body that sets one is forwarded with it untouched (no clamping
either way). A device's registry `max_tokens_default` overrides the gate's
(0 = never for that device). 0 / unset disables it. Streaming bodies too. The
eval opt-out header (X-OpenBeast-Gate-Defaults: off) skips it. A body the
gate cannot parse is refused exactly as before. Each injection is counted
(openbeast_edge_max_tokens_defaulted_total, per device), never logged with
content. Why: docs/SPARK_HYDRA.md "beast-gate's default max_tokens".

Run: pytest tests/test_gate_max_tokens.py
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys

import pytest
from starlette.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_edge import DEVICE_KEY, _local_headers, _registry, _stub_upstream  # noqa: E402

AUTH = {"Authorization": f"Bearer {DEVICE_KEY}", "Content-Type": "application/json"}
CHAT = {"model": "deepseek", "messages": [{"role": "user", "content": "hi"}]}
COMP = {"model": "deepseek", "prompt": "hi"}
CHAT_PATH, COMP_PATH = "/v1/chat/completions", "/v1/completions"


def _gate(tmp_path, monkeypatch, default, *, device_override=None):
    monkeypatch.setenv("OPENBEAST_REPO_DIR", str(tmp_path))
    for k in ("OPENBEAST_EDGE_ALLOW_ANON", "OPENBEAST_API_KEY",
              "OPENBEAST_INFERENCE_THINKING"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENBEAST_EDGE_MAX_INFLIGHT", "4")
    if default is None:
        monkeypatch.delenv("OPENBEAST_GATE_DEFAULT_MAX_TOKENS", raising=False)
    else:
        monkeypatch.setenv("OPENBEAST_GATE_DEFAULT_MAX_TOKENS", default)
    import edge
    importlib.reload(edge)
    reg = _registry(tmp_path)
    if device_override is not None:
        doc = json.loads(reg.read_text())
        doc["devices"][0]["max_tokens_default"] = device_override
        reg.write_text(json.dumps(doc))
    return edge


def _forward(edge, body, path=CHAT_PATH, headers=None, raw=None):
    captured = {}
    _stub_upstream(edge, captured)
    with TestClient(edge.app) as c:
        r = c.post(path, content=raw if raw is not None else json.dumps(body).encode(),
                   headers={**AUTH, **(headers or {})})
        metrics = c.get("/gate/metrics", headers=_local_headers(edge)).text
    return r, (json.loads(captured["content"]) if "content" in captured else None), metrics


def _defaulted(metrics: str) -> int:
    n = 0
    for line in metrics.splitlines():
        if line.startswith("openbeast_edge_max_tokens_defaulted_total{"):
            n += int(line.rsplit(" ", 1)[1])
    return n


# --- the pure rule -----------------------------------------------------------

def test_absent_gets_the_default():
    import edge
    body = dict(CHAT)
    assert edge._apply_max_tokens_default(body, 8192) is True
    assert body["max_tokens"] == 8192


@pytest.mark.parametrize("key", ["max_tokens", "max_completion_tokens", "n_predict"])
@pytest.mark.parametrize("value", [1, 100, 8192, 200_000])
def test_a_set_cap_is_never_changed(key, value):
    import edge
    body = {**CHAT, key: value}
    assert edge._apply_max_tokens_default(body, 8192) is False
    assert body == {**CHAT, key: value}           # no clamp up, no clamp down, no extra key


def test_null_counts_as_absent():
    import edge
    body = {**CHAT, "max_tokens": None}
    assert edge._apply_max_tokens_default(body, 8192) is True
    assert body["max_tokens"] == 8192


def test_zero_disables():
    import edge
    body = dict(CHAT)
    assert edge._apply_max_tokens_default(body, 0) is False
    assert body == CHAT


@pytest.mark.parametrize("raw,want", [("", 0), ("0", 0), ("8192", 8192),
                                      ("8192  # DeepSeek", 8192), ("lots", 0), ("-5", 0)])
def test_the_conf_value(raw, want):
    import edge
    assert edge.max_tokens_default({"OPENBEAST_GATE_DEFAULT_MAX_TOKENS": raw}) == want
    assert edge.max_tokens_default({}) == 0


@pytest.mark.parametrize("override,want", [(None, 8192), (2048, 2048), (0, 0),
                                           (True, 8192), ("4096", 8192), (-1, 8192)])
def test_device_override(override, want):
    import edge
    dev = {"id": "x"} if override is None else {"id": "x", "max_tokens_default": override}
    assert edge.device_max_tokens(dev, 8192) == want


# --- through the gate ------------------------------------------------------

@pytest.mark.parametrize("path,body", [(CHAT_PATH, CHAT), (COMP_PATH, COMP)])
def test_gate_injects_when_absent(tmp_path, monkeypatch, path, body):
    edge = _gate(tmp_path, monkeypatch, "8192")
    r, sent, metrics = _forward(edge, body, path)
    assert r.status_code == 200, r.text
    assert sent == {**body, "max_tokens": 8192}
    assert _defaulted(metrics) == 1


@pytest.mark.parametrize("path,body", [(CHAT_PATH, CHAT), (COMP_PATH, COMP)])
@pytest.mark.parametrize("key", ["max_tokens", "max_completion_tokens"])
def test_gate_leaves_a_set_cap_alone(tmp_path, monkeypatch, path, body, key):
    edge = _gate(tmp_path, monkeypatch, "8192")
    for value in (64, 32768):
        r, sent, metrics = _forward(edge, {**body, key: value}, path)
        assert r.status_code == 200, r.text
        assert sent == {**body, key: value}
        assert _defaulted(metrics) == 0


def test_gate_streaming_is_injected_too(tmp_path, monkeypatch):
    edge = _gate(tmp_path, monkeypatch, "8192")
    r, sent, metrics = _forward(edge, {**CHAT, "stream": True})
    assert r.status_code == 200, r.text
    assert sent["max_tokens"] == 8192
    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}   # the gate's other rewrite stands
    assert _defaulted(metrics) == 1


def test_gate_keeps_its_other_rewrites(tmp_path, monkeypatch):
    """Thinking default and the server-side slot still apply alongside."""
    monkeypatch.setenv("OPENBEAST_INFERENCE_THINKING", "on")
    edge = _gate(tmp_path, monkeypatch, "8192")
    monkeypatch.setenv("OPENBEAST_INFERENCE_THINKING", "on")
    importlib.reload(edge)
    r, sent, _ = _forward(edge, {**CHAT, "id_slot": 3})
    assert r.status_code == 200, r.text
    assert sent["chat_template_kwargs"] == {"enable_thinking": True}
    assert "id_slot" not in sent                               # client slot still stripped
    assert sent["max_tokens"] == 8192
    assert sent["messages"] == CHAT["messages"]


@pytest.mark.parametrize("default", [None, "0"])
def test_gate_disabled_forwards_unchanged(tmp_path, monkeypatch, default):
    edge = _gate(tmp_path, monkeypatch, default)
    r, sent, metrics = _forward(edge, CHAT)
    assert r.status_code == 200, r.text
    assert sent == CHAT
    assert _defaulted(metrics) == 0


def test_device_override_wins(tmp_path, monkeypatch):
    edge = _gate(tmp_path, monkeypatch, "8192", device_override=2048)
    _, sent, metrics = _forward(edge, CHAT)
    assert sent["max_tokens"] == 2048
    assert _defaulted(metrics) == 1


def test_device_override_zero_opts_that_device_out(tmp_path, monkeypatch):
    edge = _gate(tmp_path, monkeypatch, "8192", device_override=0)
    _, sent, metrics = _forward(edge, CHAT)
    assert sent == CHAT
    assert _defaulted(metrics) == 0


def test_device_override_with_the_gate_default_off(tmp_path, monkeypatch):
    edge = _gate(tmp_path, monkeypatch, None, device_override=4096)
    _, sent, _ = _forward(edge, CHAT)
    assert sent["max_tokens"] == 4096


def test_eval_opt_out_header_skips_it(tmp_path, monkeypatch):
    edge = _gate(tmp_path, monkeypatch, "8192")
    _, sent, metrics = _forward(edge, CHAT, headers={"X-OpenBeast-Gate-Defaults": "off"})
    assert sent == CHAT
    assert _defaulted(metrics) == 0


def test_embeddings_never_touched(tmp_path, monkeypatch):
    edge = _gate(tmp_path, monkeypatch, "8192")
    body = {"model": "e", "input": "hi"}
    _, sent, _ = _forward(edge, body, "/v1/embeddings")
    assert sent == body


@pytest.mark.parametrize("raw", [b'{"messages": [', b"not json", b"[1, 2]"])
def test_malformed_body_is_refused_as_before(tmp_path, monkeypatch, raw):
    """Unchanged: the gate never forwards a body it cannot parse (400), and
    nothing is counted."""
    edge = _gate(tmp_path, monkeypatch, "8192")
    r, sent, metrics = _forward(edge, None, raw=raw)
    assert r.status_code == 400
    assert sent is None
    assert _defaulted(metrics) == 0


def test_the_count_is_per_device_and_holds_no_content(tmp_path, monkeypatch):
    edge = _gate(tmp_path, monkeypatch, "8192")
    _stub_upstream(edge, {})
    secret = "a prompt that must never reach the metrics"
    with TestClient(edge.app) as c:
        for _ in range(3):
            c.post(CHAT_PATH, content=json.dumps(
                {**CHAT, "messages": [{"role": "user", "content": secret}]}).encode(),
                headers=AUTH)
        c.post(CHAT_PATH, content=json.dumps({**CHAT, "max_tokens": 10}).encode(), headers=AUTH)
        local = c.get("/gate/metrics", headers=_local_headers(edge)).text
        tenant = c.get("/gate/metrics", headers=AUTH).text
        health = c.get("/gate/health", headers=_local_headers(edge)).json()
    assert 'openbeast_edge_max_tokens_defaulted_total{device="laptop"} 3' in local
    assert 'openbeast_edge_max_tokens_defaulted_total{device="laptop"} 3' in tenant
    assert secret not in local
    assert health["max_tokens_default"] == 8192


# --- conf.sh: where the default comes from ------------------------------------

def _conf(tmp_path, body, **env):
    sb = tmp_path / "rig"
    (sb / "scripts" / "lib").mkdir(parents=True, exist_ok=True)
    src = os.path.join(ROOT, "scripts", "lib", "conf.sh")
    (sb / "scripts" / "lib" / "conf.sh").write_text(open(src).read())
    (sb / "openbeast.conf").write_text(body + "\n")
    e = {"HOME": str(tmp_path), "PATH": os.environ["PATH"], "REPO_DIR": str(sb), **env}
    out = subprocess.run(
        ["bash", "-c", f"source '{sb}/scripts/lib/conf.sh' >/dev/null 2>&1; "
                       "printf '%s' \"${OPENBEAST_GATE_DEFAULT_MAX_TOKENS-ABSENT}\""],
        env=e, capture_output=True, text=True, timeout=60)
    return out.stdout


@pytest.mark.parametrize("body,env,want", [
    ("AGENT_PROFILE=deepseek-best", {}, "8192"),          # the Spark pair
    ("AGENT_PROFILE=inkling-best", {}, "ABSENT"),         # the RTX 5090: off
    ("AGENT_PROFILE=qwen38-best", {}, "ABSENT"),
    ("# nothing", {}, "ABSENT"),
    ("AGENT_PROFILE=deepseek-best\nGATE_DEFAULT_MAX_TOKENS=4096", {}, "4096"),
    ("AGENT_PROFILE=deepseek-best\nGATE_DEFAULT_MAX_TOKENS=0", {}, "0"),
    ("AGENT_PROFILE=inkling-best\nGATE_DEFAULT_MAX_TOKENS=8192  # rig", {}, "8192"),
    ("GATE_DEFAULT_MAX_TOKENS=4096", {"OPENBEAST_GATE_DEFAULT_MAX_TOKENS": "1024"}, "1024"),
])
def test_conf_resolution(tmp_path, body, env, want):
    assert _conf(tmp_path, body, **env) == want
