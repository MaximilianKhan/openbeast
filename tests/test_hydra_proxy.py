#!/usr/bin/env python3
"""beast-hydra proxy (agents/hydra.py): a real uvicorn hydra on an ephemeral
loopback port in front of fake engines (tests/fakes/fake_engine.py). No GPU,
no real engine, no network beyond 127.0.0.1 (docs/BEAST_HYDRA_PLAN.md §6.11).

Run: python3 -m pytest tests/test_hydra_proxy.py -q
"""
from __future__ import annotations

import copy
import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "agents"))
sys.path.insert(0, str(REPO / "tests" / "fakes"))
sys.path.insert(0, str(REPO / "scripts" / "backends" / "pylib"))

import uvicorn  # noqa: E402

import conformance  # noqa: E402
import hydra  # noqa: E402
import hydra_core as core  # noqa: E402
from fake_engine import FakeEngine  # noqa: E402

INBOUND = "inbound-key-123"
RIG_KEY, SPARK_KEY = "rig-node-key", "spark-node-key"
SENTINEL = "SENTINEL-PROMPT-7f3a9c"


# ───────────────────────────── harness ─────────────────────────────

class Server:
    """hydra in a uvicorn thread on 127.0.0.1:<ephemeral>."""

    def __init__(self, cfg: core.Config, env: dict, run: Path, cfg_path: Path | None = None):
        self.hy = hydra.Hydra(cfg, env=env, run=run, cfg_path=cfg_path)
        self.app = hydra.create_app(self.hy)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(64)
        self.port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="error", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [sock]}, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            assert time.time() < deadline, "hydra did not start"
            time.sleep(0.02)

    def close(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def local(self) -> dict:
        return {"X-OpenBeast-Local": self.hy.local_token}


def raw_config(rig: FakeEngine, sparks: FakeEngine, tf: FakeEngine, tmp: Path, **over) -> dict:
    raw = {
        "schema": 1,
        "hydra": {"probe_interval_s": 1, "probe_down_interval_s": 1, "models_interval_s": 10,
                  "down_after": 1, "up_after": 1, "pre_commit_budget_s": 20,
                  "audit": str(tmp / "audit.jsonl"),
                  "breaker": {"fail_threshold": 3, "open_s": 30, "success_threshold": 1},
                  "instinct": {"url": "http://127.0.0.1:9", "key_file": str(tmp / "instinct.key")}},
        "nodes": {
            "rig": {"url": rig.url, "engine": "llama", "slots": 1, "key_env": "RIG_KEY",
                    "ttft_timeout_s": 3, "idle_timeout_s": 3, "nonstream_timeout_s": 5},
            "sparks": {"url": sparks.url, "engine": "vllm", "slots": 2, "key_env": "SPARK_KEY",
                       "ttft_timeout_s": 3, "idle_timeout_s": 3, "nonstream_timeout_s": 5},
            "tf": {"url": tf.url, "engine": "tensorfold", "slots": 2,
                   "ttft_timeout_s": 3, "idle_timeout_s": 3, "nonstream_timeout_s": 5},
        },
        "deployments": {
            "unc@rig": {"node": "rig", "upstream": rig.model, "ctx": 262144, "family": "unc",
                        "caps": ["tools", "json_schema", "grammar", "vision", "reasoning_budget", "id_slot",
                                 "embeddings"]},
            "nvfp4@sparks": {"node": "sparks", "upstream": sparks.model, "ctx": 262144, "family": "stock",
                             "caps": ["tools", "json_schema", "reasoning_budget"]},
            "mlx@tf": {"node": "tf", "upstream": tf.model, "ctx": 65536, "family": "stock", "caps": ["tools"]},
        },
        "routes": {
            "beast": {"targets": [{"d": "unc@rig", "priority": 0}, {"d": "nvfp4@sparks", "priority": 1}],
                      "aliases": ["qwen-27b-q5", "default"], "description": "daily driver"},
            "beast:fast": {"targets": [{"d": "nvfp4@sparks", "priority": 0}, {"d": "unc@rig", "priority": 1}]},
            "beast:tf": {"targets": [{"d": "mlx@tf"}, {"d": "unc@rig", "priority": 1}]},
            "solo": {"targets": [{"d": "unc@rig"}], "listed": False},
            "retry": {"targets": [{"d": "unc@rig"}, {"d": "nvfp4@sparks", "priority": 1}],
                      "retry_on_ttft_timeout": True},
        },
        "rules": [{"name": "phone-fast", "when": {"device": "max-phone", "model": "beast"},
                   "then": {"route": "beast:fast"}}],
    }
    for k, v in over.items():
        raw[k] = v
    return raw


def env_for(tmp: Path, **extra) -> dict:
    tok = tmp / "caller.token"
    tok.write_text("caller-secret\n")
    tok.chmod(0o600)
    e = {"LLAMA_API_KEY": INBOUND, "RIG_KEY": RIG_KEY, "SPARK_KEY": SPARK_KEY,
         "OPENBEAST_HYDRA_CALLER_TOKEN_FILE": str(tok), "OPENBEAST_EDGE_READ_TIMEOUT": "600"}
    e.update(extra)
    return e


@pytest.fixture
def fleet(tmp_path):
    made = {"engines": [], "servers": []}

    def make(raw_mut=None, env_extra=None, chunks=5, tok_ms=0, cfg_file=False):
        rig = FakeEngine("llama", "qwen-unc", RIG_KEY, slots=1, chunks=chunks, tok_ms=tok_ms)
        sparks = FakeEngine("vllm", "qwen3.8-27b-nvfp4", SPARK_KEY, slots=2, chunks=chunks, tok_ms=tok_ms)
        tf = FakeEngine("tensorfold", "local-model", None, slots=2, chunks=chunks, tok_ms=tok_ms)
        made["engines"] += [rig, sparks, tf]
        raw = raw_config(rig, sparks, tf, tmp_path)
        if raw_mut:
            raw_mut(raw)
        env = env_for(tmp_path, **(env_extra or {}))
        cfg = core.validate(raw, env)
        path = None
        if cfg_file:
            path = tmp_path / "hydra.toml"
            path.write_text(core.to_toml(raw))
        srv = Server(cfg, env, tmp_path / "run", path)
        made["servers"].append(srv)
        srv.raw = raw
        wait_ready(srv, [d for d in cfg.deployments])
        return srv, rig, sparks, tf

    yield make
    for s in made["servers"]:
        s.close()
    for e in made["engines"]:
        e.stop()


def wait_ready(srv, deps, state=core.READY, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        hs = srv.hy.state.health
        if all(hs[d].h.state == state for d in deps if d in hs):
            return
        time.sleep(0.05)
    raise AssertionError({d: srv.hy.state.health[d].h.state for d in deps})


def auth(extra=None) -> dict:
    h = {"Authorization": f"Bearer {INBOUND}"}
    h.update(extra or {})
    return h


def chat(model="beast", content="hi", **kw) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": content}], **kw}


def post(srv, body, headers=None, path="/v1/chat/completions", timeout=30):
    return httpx.post(srv.url + path, json=body, headers=auth(headers), timeout=timeout)


def audit_rows(tmp) -> list[dict]:
    p = tmp / "audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def posts(eng) -> list[dict]:
    return [r for r in eng.requests if r["method"] == "POST"]


def sse_events(text: str) -> list[str]:
    return [ln[6:] for ln in text.split("\n") if ln.startswith("data: ")]


# ───────────────────────────── streaming + SDK ─────────────────────────────

def test_stream_passthrough_is_byte_identical(fleet):
    srv, rig, _, _ = fleet()
    body = chat(stream=True, stream_options={"include_usage": True})
    direct = httpx.post(rig.url + "/v1/chat/completions", json=dict(body, model="qwen-unc"),
                        headers={"Authorization": f"Bearer {RIG_KEY}"}).content
    r = post(srv, body)
    assert r.status_code == 200 and r.content == direct
    assert r.content.rstrip().endswith(b"data: [DONE]")
    assert r.headers["x-hydra-deployment"] == "unc@rig"


def test_openai_sdk_consumes_the_stream_and_raises_on_the_error_event(fleet):
    openai = pytest.importorskip("openai")
    srv, rig, _, _ = fleet()
    c = openai.OpenAI(base_url=srv.url + "/v1", api_key=INBOUND, max_retries=0)
    text = "".join((ch.choices[0].delta.content or "") for ch in
                   c.chat.completions.create(model="beast", messages=[{"role": "user", "content": "x"}],
                                             stream=True) if ch.choices)
    assert text.startswith("tok0 ")
    rig.set_fault("die_after_chunks", 1)
    with pytest.raises(openai.APIError):
        for _ in c.chat.completions.create(model="solo", messages=[{"role": "user", "content": "x"}],
                                           stream=True, extra_headers={"X-Fake-Fault": "die_after_chunks:3"}):
            pass


def test_non_stream_and_embeddings(fleet):
    srv, rig, _, _ = fleet()
    r = post(srv, chat())
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "hello from llama"
    assert r.json()["model"] == "qwen-unc"                  # served id, as the engine said it
    r = post(srv, {"model": "beast", "input": "abc"}, path="/v1/embeddings")
    assert r.status_code == 200 and r.json()["data"][0]["embedding"]


# ───────────────────────────── pre-commit failover ─────────────────────────────

@pytest.mark.parametrize("fault", ["http_500", "http_503", "headers_then_close", "http_429", "loading_503"])
def test_precommit_failover(fleet, fault, tmp_path):
    srv, rig, sparks, _ = fleet()
    rig.set_fault(fault, 1)
    r = post(srv, chat(stream=True))
    assert r.status_code == 200, r.text
    assert r.headers["x-hydra-deployment"] == "nvfp4@sparks"
    att = r.headers["x-hydra-attempts"].split(",")
    assert att[0].startswith("unc@rig:") and att[1] == "nvfp4@sparks:200"
    hs = srv.hy.state.health["unc@rig"].h
    if fault == "http_429":
        assert hs.fails == 0, "429 is not a breaker failure"
    elif fault == "loading_503":
        assert hs.state == core.LOADING and hs.fails == 0
    else:
        assert hs.fails == 1


def test_refused_node_fails_over(fleet):
    srv, rig, sparks, _ = fleet()
    rig.stop()
    r = post(srv, chat())
    assert r.status_code == 200 and r.headers["x-hydra-attempts"] == "unc@rig:connect,nvfp4@sparks:200"


def test_node_401_is_auth_failed_and_never_the_callers_401(fleet):
    srv, rig, sparks, _ = fleet()
    rig.set_fault("http_401", 1)
    r = post(srv, chat())
    assert r.status_code == 200 and r.headers["x-hydra-deployment"] == "nvfp4@sparks"
    assert srv.hy.state.health["unc@rig"].h.state == core.AUTH_FAILED
    srv.hy.state.health["unc@rig"].h.state = core.READY
    rig.set_fault("http_401", 1)
    r = post(srv, chat(model="solo"))
    assert r.status_code == 502 and r.json()["error"]["type"] == "hydra_upstream_auth"


def test_model_404_is_mismatch_and_fails_over(fleet):
    srv, rig, sparks, _ = fleet()
    sparks.set_fault("http_404_model", 1)
    r = post(srv, chat(model="beast:fast"))
    assert r.status_code == 200 and r.headers["x-hydra-deployment"] == "unc@rig"
    assert srv.hy.state.health["nvfp4@sparks"].h.state == core.MISMATCH


def test_models_check_marks_mismatch_and_auth(fleet):
    srv, rig, sparks, _ = fleet()
    sparks.set_fault("wrong_model")
    srv.hy.next_models.clear()
    wait_ready(srv, ["nvfp4@sparks"], core.MISMATCH)
    sparks.set_fault("models_401")
    wait_ready(srv, ["nvfp4@sparks"], core.AUTH_FAILED)
    sparks.set_fault(None)
    wait_ready(srv, ["nvfp4@sparks"], core.READY)


# ───────────────────────────── 4xx passthrough ─────────────────────────────

@pytest.mark.parametrize("route,dep_eng", [("solo", "rig"), ("beast:fast", "sparks"), ("beast:tf", "tf")])
def test_overflow_400_passes_through_byte_for_byte(fleet, route, dep_eng):
    srv, rig, sparks, tf = fleet()
    eng = {"rig": rig, "sparks": sparks, "tf": tf}[dep_eng]
    key = {"rig": RIG_KEY, "sparks": SPARK_KEY, "tf": None}[dep_eng]
    direct = httpx.post(eng.url + "/v1/chat/completions", json=chat(model=eng.model),
                        headers={"X-Fake-Fault": "overflow_400",
                                 **({"Authorization": f"Bearer {key}"} if key else {})})
    others = [e for e in (rig, sparks, tf) if e is not eng]
    before = [len(posts(e)) for e in others]
    r = post(srv, chat(model=route), {"X-Fake-Fault": "overflow_400"})
    assert r.status_code == 400 and r.content == direct.content
    assert r.headers["content-type"] == direct.headers["content-type"]
    assert conformance.runner_overflow_re().search(r.text), "runner.py must still recognise the overflow"
    assert [len(posts(e)) for e in others] == before, "a 4xx must never fail over"
    assert r.headers["x-hydra-deployment"] and r.headers["x-hydra-request-id"]


# ───────────────────────────── deadlines + mid-stream ─────────────────────────────

def test_ttft_timeout_is_504_without_failover_unless_opted_in(fleet):
    srv, rig, sparks, _ = fleet(lambda r: r["nodes"]["rig"].update(ttft_timeout_s=0.4))
    rig.set_fault("ttft_ms:1500", 1)
    r = post(srv, chat(stream=True))
    assert r.status_code == 504 and r.json()["error"]["type"] == "hydra_timeout"
    assert not posts(sparks)
    rig.set_fault("ttft_ms:1500", 1)
    r = post(srv, chat(model="retry", stream=True))
    assert r.status_code == 200 and r.headers["x-hydra-deployment"] == "nvfp4@sparks"
    assert r.headers["x-hydra-attempts"].startswith("unc@rig:timeout,")


def test_nonstream_deadline(fleet):
    srv, rig, _, _ = fleet(lambda r: r["nodes"]["rig"].update(nonstream_timeout_s=0.4))
    rig.set_fault("ttft_ms:1500", 1)
    assert post(srv, chat(model="solo")).status_code == 504


@pytest.mark.parametrize("fault", ["die_after_chunks:3", "stall_after_chunks:3:3000"])
def test_midstream_failure_is_loud_and_never_replayed(fleet, fault, tmp_path):
    srv, rig, sparks, _ = fleet(lambda r: r["nodes"]["rig"].update(idle_timeout_s=0.5))
    r = post(srv, chat(model="beast", stream=True, content=SENTINEL), {"X-Fake-Fault": fault})
    ev = sse_events(r.text)
    assert r.status_code == 200 and "[DONE]" not in ev
    err = json.loads(ev[-1])["error"]
    assert err["type"] == "hydra_upstream_error" and err["code"] == "upstream_failed_midstream"
    assert err["hydra_deployment"] == "unc@rig"
    assert not posts(sparks), "never a replay"
    time.sleep(0.2)
    assert srv.hy.state.health["unc@rig"].h.fail_total == 1
    row = audit_rows(tmp_path)[-1]
    assert row["outcome"] == "upstream_failed_midstream" and row["deployment"] == "unc@rig"
    assert srv.hy.state.inflight("unc@rig") == 0


# ───────────────────────────── strict ─────────────────────────────

def test_strict_pins(fleet):
    srv, rig, sparks, _ = fleet()
    rig.set_fault("health_down")
    wait_ready(srv, ["unc@rig"], core.DOWN)
    r = post(srv, chat(model="unc@rig"))
    assert r.status_code == 503 and r.json()["error"]["type"] == "hydra_pinned_unavailable"
    assert r.headers["retry-after"] == "5"
    r = httpx.post(srv.url + "/pin/unc@rig/v1/chat/completions", json=chat(), headers=auth())
    assert r.status_code == 503
    r = post(srv, chat(), {"X-Hydra-Pin": "unc@rig"})
    assert r.status_code == 503, "a pin never substitutes, even with healthy alternates"
    assert not posts(sparks)
    r = post(srv, chat(model="nvfp4@sparks", grammar="root ::= x"))
    assert r.status_code == 422 and r.json()["error"]["type"] == "hydra_pin_incompatible"
    r = httpx.post(srv.url + "/pin/nvfp4@sparks/v1/chat/completions", json=chat(model="whatever"),
                   headers=auth())
    assert r.status_code == 200 and r.headers["x-hydra-route"] == "pin"
    assert posts(sparks)[-1]["body"]["model"] == "qwen3.8-27b-nvfp4"


def test_pin_passthrough_paths(fleet):
    srv, rig, sparks, _ = fleet()
    r = httpx.get(srv.url + "/pin/unc@rig/props", headers=auth())
    assert r.status_code == 200 and r.json()["total_slots"] == 1
    assert rig.requests[-1]["headers"].get("authorization") == f"Bearer {RIG_KEY}"
    assert httpx.get(srv.url + "/pin/unc@rig/slots", headers=auth()).status_code == 200
    assert httpx.get(srv.url + "/pin/nvfp4@sparks/props", headers=auth()).status_code == 404
    r = httpx.get(srv.url + "/pin/nvfp4@sparks/v1/models", headers=auth())
    assert [m["id"] for m in r.json()["data"]] == ["nvfp4@sparks"]
    assert httpx.get(srv.url + "/pin/ghost/v1/models", headers=auth()).status_code == 404
    assert httpx.get(srv.url + "/pin/unc@rig/props").status_code == 401


# ───────────────────────────── body fidelity ─────────────────────────────

from test_hydra_core import BODIES  # noqa: E402


def test_body_fidelity_on_the_wire(fleet):
    srv, rig, sparks, tf = fleet()
    for body in BODIES:
        for model, eng in (("unc@rig", rig), ("nvfp4@sparks", sparks)):
            b = copy.deepcopy(body)
            b["model"] = model
            b.pop("grammar", None) if eng is sparks else None
            b.pop("stream", None)
            b.pop("stream_options", None)
            if eng is sparks and (core.extract_features("/v1/chat/completions", b, {}, srv.hy.cfg).has_images):
                continue
            r = post(srv, b)
            assert r.status_code == 200, (model, r.text)
            got = posts(eng)[-1]["body"]
            assert got == dict(b, model=eng.model), model


def test_id_slot_rule_on_the_wire(fleet):
    srv, rig, sparks, tf = fleet()
    post(srv, chat(model="unc@rig", id_slot=0))
    assert posts(rig)[-1]["body"]["id_slot"] == 0
    post(srv, chat(model="unc@rig", id_slot=3))                 # rig has 1 slot
    assert "id_slot" not in posts(rig)[-1]["body"]
    post(srv, chat(model="nvfp4@sparks", id_slot=0))
    assert "id_slot" not in posts(sparks)[-1]["body"]
    post(srv, chat(model="mlx@tf", id_slot=0))
    assert "id_slot" not in posts(tf)[-1]["body"]


# ───────────────────────────── keys + trust ─────────────────────────────

def test_inbound_key_and_node_keys(fleet):
    srv, rig, sparks, tf = fleet()
    r = httpx.post(srv.url + "/v1/chat/completions", json=chat())
    assert r.status_code == 401 and r.json()["error"]["type"] == "hydra_unauthorized"
    r = httpx.post(srv.url + "/v1/chat/completions", json=chat(), headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    r = httpx.post(srv.url + "/v1/chat/completions", json=chat(),
                   headers={"Authorization": b"Bearer \xff\xfe"})     # hostile bytes: no 500
    assert r.status_code == 401
    assert httpx.get(srv.url + "/v1/models").status_code == 401
    post(srv, chat(model="unc@rig"))
    post(srv, chat(model="nvfp4@sparks"))
    post(srv, chat(model="mlx@tf"))
    assert posts(rig)[-1]["headers"]["authorization"] == f"Bearer {RIG_KEY}"
    assert posts(sparks)[-1]["headers"]["authorization"] == f"Bearer {SPARK_KEY}"
    assert "authorization" not in posts(tf)[-1]["headers"], "never a key to TensorFold"
    for e in (rig, sparks, tf):
        for req in e.requests:
            assert INBOUND not in json.dumps(req), "the inbound key must never travel upstream"
    assert all(r["method"] != "GET" or "authorization" not in r["headers"]
               for r in tf.requests), "not even on probes"


def test_identity_headers_need_the_caller_token(fleet, tmp_path):
    srv, rig, sparks, _ = fleet()
    spoof = {"X-OpenBeast-Device": "max-phone", "X-OpenWebUI-User-Role": "admin",
             "X-Hydra-Caller": "wrong"}
    r = post(srv, chat(), spoof)
    assert r.headers["x-hydra-route"] == "beast" and r.headers["x-hydra-deployment"] == "unc@rig"
    fwd = posts(rig)[-1]["headers"]
    assert "x-openbeast-device" not in fwd and "x-openwebui-user-role" not in fwd
    assert not any(k.startswith("x-hydra") for k in fwd)
    assert audit_rows(tmp_path)[-1]["trusted"] is False
    r = post(srv, chat(), {"X-OpenBeast-Device": "max-phone", "X-Hydra-Caller": "caller-secret"})
    assert r.headers["x-hydra-route"] == "beast:fast" and r.headers["x-hydra-rule"] == "phone-fast"
    assert posts(sparks)[-1]["headers"]["x-openbeast-device"] == "max-phone"
    assert audit_rows(tmp_path)[-1]["device"] == "max-phone"


def test_request_id_is_forwarded(fleet):
    srv, rig, _, _ = fleet()
    r = post(srv, chat(), {"X-OpenBeast-Request-Id": "gate0123abcd"})
    assert r.headers["x-hydra-request-id"] == "gate0123abcd"
    assert posts(rig)[-1]["headers"]["x-openbeast-request-id"] == "gate0123abcd"
    r = post(srv, chat(), {"X-OpenBeast-Request-Id": "bad id!"})
    assert r.headers["x-hydra-request-id"] != "bad id!"


# ───────────────────────────── provenance + audit ─────────────────────────────

AUDIT_KEYS = {"ts", "request_id", "device", "role", "trusted", "requested", "strict", "rules", "route",
              "features", "excluded", "attempts", "deployment", "status", "outcome", "ms", "usage",
              "body_edits", "config", "instinct", "path"}


def test_provenance_headers_and_audit(fleet, tmp_path):
    srv, rig, _, _ = fleet()
    r = post(srv, chat(content=SENTINEL, stream=True, stream_options={"include_usage": True}))
    h = r.headers
    assert (h["x-hydra-route"], h["x-hydra-deployment"], h["x-hydra-node"], h["x-hydra-engine"],
            h["x-hydra-upstream-model"]) == ("beast", "unc@rig", "rig", "llama", "qwen-unc")
    assert h["x-hydra-attempts"] == "unc@rig:200" and h["x-hydra-config"] == srv.hy.cfg.hash
    time.sleep(0.2)
    row = audit_rows(tmp_path)[-1]
    assert AUDIT_KEYS <= set(row), AUDIT_KEYS - set(row)
    assert row["outcome"] == "ok" and row["status"] == 200 and row["body_edits"] == ["model"]
    assert row["usage"]["completion_tokens"] == 5
    assert row["attempts"][0]["ttft_ms"] is not None and row["request_id"] == h["x-hydra-request-id"]
    assert (tmp_path / "audit.jsonl").stat().st_mode & 0o077 == 0
    status = httpx.get(srv.url + "/hydra/status", headers=srv.local()).text
    decisions = httpx.get(srv.url + "/hydra/decisions", headers=srv.local()).text
    for blob in ((tmp_path / "audit.jsonl").read_text(), status, decisions):
        assert SENTINEL not in blob, "prompt text must never reach the audit, status or decisions"
    assert RIG_KEY not in status and SPARK_KEY not in status


def test_error_responses_carry_provenance_and_are_audited(fleet, tmp_path):
    srv, rig, sparks, _ = fleet()
    r = post(srv, chat(model="ghost@nowhere"))
    assert r.status_code == 200 and r.headers["x-hydra-route"] == "beast"     # unknown id → default_route
    r = post(srv, chat(), {"X-Hydra-Pin": "ghost@nowhere"})
    assert r.status_code == 404 and r.json()["error"]["type"] == "hydra_unknown_deployment"
    assert r.headers["x-hydra-config"] == srv.hy.cfg.hash
    assert audit_rows(tmp_path)[-1]["outcome"] == "unknown_deployment"
    r = httpx.post(srv.url + "/v1/chat/completions", content=b"{nope", headers=auth())
    assert r.status_code == 400 and r.json()["error"]["type"] == "hydra_bad_request"


def test_body_cap(fleet, monkeypatch):
    srv, _, _, _ = fleet()
    monkeypatch.setattr(hydra, "MAX_BODY_BYTES", 1000)
    assert post(srv, chat(content="x" * 5000)).status_code == 413


# ───────────────────────────── catalog + health ─────────────────────────────

def test_models_catalog(fleet):
    srv, _, _, _ = fleet()
    data = httpx.get(srv.url + "/v1/models", headers=auth()).json()["data"]
    idsl = [m["id"] for m in data]
    assert idsl[0] == "beast" and "solo" not in idsl
    assert idsl.index("beast:fast") < idsl.index("unc@rig")        # routes before deployments
    b = data[0]["openbeast"]
    assert b["kind"] == "route" and b["healthy"] and not b["strict"] and b["description"] == "daily driver"
    assert b["max_ctx"] == 262144 and "vision" in b["caps"]
    dep = next(m for m in data if m["id"] == "unc@rig")["openbeast"]
    assert dep["kind"] == "deployment" and dep["strict"]


def test_health_follows_the_default_route(fleet):
    srv, rig, sparks, _ = fleet()
    assert httpx.get(srv.url + "/health").json() == {"status": "ok"}
    rig.set_fault("health_down")
    sparks.set_fault("health_down")
    wait_ready(srv, ["unc@rig", "nvfp4@sparks"], core.DOWN)
    r = httpx.get(srv.url + "/health")
    assert r.status_code == 503 and r.json()["status"] == "loading"
    rig.set_fault(None)
    wait_ready(srv, ["unc@rig"], core.READY)
    assert httpx.get(srv.url + "/health").status_code == 200


def test_unrouted_paths_and_host_pinning(fleet):
    srv, _, _, _ = fleet()
    r = httpx.get(srv.url + "/props", headers=auth())
    assert r.status_code == 404 and r.json()["error"]["type"] == "hydra_not_routed"
    r = httpx.get(srv.url + "/health", headers={"Host": "evil.example.com"})
    assert r.status_code == 400, "DNS-rebinding hosts are refused"


# ───────────────────────────── drain + lease ─────────────────────────────

def test_gpu_lease_drains_the_rig(fleet, tmp_path):
    flag = tmp_path / "held"
    stub = tmp_path / "lease.sh"
    stub.write_text(f"#!/bin/bash\necho \"$@\" >> {tmp_path}/lease.calls\n"
                    f"[[ -f {flag} ]] && exit 4\nexit 3\n")
    stub.chmod(0o755)

    def mut(raw):
        raw["nodes"]["rig"]["gpu_lease"] = True
    srv, rig, sparks, _ = fleet(mut, {"OPENBEAST_HYDRA_LEASE_CMD": str(stub)})
    assert post(srv, chat()).headers["x-hydra-deployment"] == "unc@rig"
    flag.write_text("x")
    deadline = time.time() + 5
    while srv.hy.state.drained.get("rig") != "lease" and time.time() < deadline:
        time.sleep(0.05)
    assert srv.hy.state.drained.get("rig") == "lease"
    r = post(srv, chat())
    assert r.headers["x-hydra-deployment"] == "nvfp4@sparks"
    assert post(srv, chat(model="unc@rig")).status_code == 503
    flag.unlink()
    deadline = time.time() + 5
    while "rig" in srv.hy.state.drained and time.time() < deadline:
        time.sleep(0.05)
    assert "rig" not in srv.hy.state.drained
    assert (tmp_path / "lease.calls").read_text().split("\n")[0] == "check"


def test_admin_drain_undrain(fleet):
    srv, rig, sparks, _ = fleet()
    assert httpx.post(srv.url + "/hydra/drain/rig", headers=srv.local()).json()["drained"] == "manual"
    assert post(srv, chat()).headers["x-hydra-deployment"] == "nvfp4@sparks"
    assert httpx.post(srv.url + "/hydra/drain/ghost", headers=srv.local()).status_code == 404
    httpx.post(srv.url + "/hydra/undrain/rig", headers=srv.local())
    assert post(srv, chat()).headers["x-hydra-deployment"] == "unc@rig"


def test_admin_routes_need_the_local_token(fleet):
    srv, _, _, _ = fleet()
    for m, p in (("GET", "/hydra/status"), ("POST", "/hydra/explain"), ("GET", "/hydra/decisions"),
                 ("POST", "/hydra/reload"), ("POST", "/hydra/drain/rig"), ("POST", "/hydra/undrain/rig"),
                 ("GET", "/hydra/metrics")):
        r = httpx.request(m, srv.url + p, headers=auth())          # the inference key is not enough
        assert r.status_code == 403, p
        r = httpx.request(m, srv.url + p, headers={"X-OpenBeast-Local": "guess"})
        assert r.status_code == 403, p
    tok = (srv.hy.run / "hydra-local.token")
    assert tok.read_text() == srv.hy.local_token and tok.stat().st_mode & 0o077 == 0


def test_explain_and_metrics(fleet):
    srv, _, _, _ = fleet()
    body = {"model": "beast", "messages": [{"role": "user", "content": "hi"}],
            "headers": {"X-OpenBeast-Device": "max-phone"}, "trusted": "true"}
    r = httpx.post(srv.url + "/hydra/explain", json=body, headers=srv.local()).json()
    assert r["route"] == "beast:fast" and r["trace"]["rules"] == ["phone-fast"]
    assert r["attempts"][0]["d"] == "nvfp4@sparks" and r["body_edits"] == ["model"]
    post(srv, chat())
    m = httpx.get(srv.url + "/hydra/metrics", headers=srv.local()).text
    assert 'hydra_requests_total{route="beast",deployment="unc@rig",outcome="ok"} 1' in m
    assert f'hydra_config_info{{hash="{srv.hy.cfg.hash}"}} 1' in m
    assert 'hydra_deployment_state{deployment="unc@rig",state="READY"} 1' in m


# ───────────────────────────── reload ─────────────────────────────

def test_reload_keeps_the_old_config_on_error_and_swaps_on_success(fleet, tmp_path):
    srv, rig, sparks, _ = fleet(cfg_file=True, tok_ms=0)
    old = srv.hy.cfg.hash
    (tmp_path / "hydra.toml").write_text("schema = 1\n[nodes.x]\nurl='nope'\n")
    r = httpx.post(srv.url + "/hydra/reload", headers=srv.local())
    assert r.status_code == 422 and not r.json()["ok"]
    st = httpx.get(srv.url + "/hydra/status", headers=srv.local()).json()
    assert st["config_hash"] == old and st["last_reload_error"]
    assert post(srv, chat()).status_code == 200
    raw = copy.deepcopy(srv.raw)
    raw["routes"]["beast"]["targets"] = [{"d": "nvfp4@sparks"}]
    (tmp_path / "hydra.toml").write_text(core.to_toml(raw))
    r = httpx.post(srv.url + "/hydra/reload", headers=srv.local())
    assert r.status_code == 200 and r.json()["config"] != old
    assert srv.hy.state.health["unc@rig"].h.state == core.READY, "learned health survives a reload"
    r = post(srv, chat())
    assert r.headers["x-hydra-deployment"] == "nvfp4@sparks" and r.headers["x-hydra-config"] != old


def test_inflight_request_finishes_on_the_old_snapshot(fleet, tmp_path):
    srv, rig, sparks, _ = fleet(cfg_file=True, chunks=10, tok_ms=80)
    got = {}

    def slow():
        with httpx.stream("POST", srv.url + "/v1/chat/completions", json=chat(stream=True),
                          headers=auth(), timeout=30) as r:
            got["dep"] = r.headers["x-hydra-deployment"]
            got["body"] = r.read()
    t = threading.Thread(target=slow)
    t.start()
    time.sleep(0.3)
    raw = copy.deepcopy(srv.raw)
    raw["routes"]["beast"]["targets"] = [{"d": "nvfp4@sparks"}]
    (tmp_path / "hydra.toml").write_text(core.to_toml(raw))
    assert httpx.post(srv.url + "/hydra/reload", headers=srv.local()).json()["ok"]
    t.join(20)
    assert got["dep"] == "unc@rig" and got["body"].rstrip().endswith(b"data: [DONE]")


# ───────────────────────────── concurrency ─────────────────────────────

def test_spill_under_concurrency(fleet):
    srv, rig, sparks, _ = fleet(chunks=10, tok_ms=60)
    deps = []

    def one():
        with httpx.stream("POST", srv.url + "/v1/chat/completions", json=chat(stream=True),
                          headers=auth(), timeout=30) as r:
            deps.append(r.headers["x-hydra-deployment"])
            r.read()
    a = threading.Thread(target=one)
    a.start()
    time.sleep(0.25)
    b = threading.Thread(target=one)
    b.start()
    a.join(20)
    b.join(20)
    assert sorted(deps) == ["nvfp4@sparks", "unc@rig"], "the 1-slot rig is busy: the second spills"


def test_cancelled_streams_release_inflight(fleet):
    srv, rig, sparks, _ = fleet(chunks=50, tok_ms=20)
    for _ in range(100):
        with httpx.stream("POST", srv.url + "/v1/chat/completions", json=chat(model="solo", stream=True),
                          headers=auth(), timeout=30) as r:
            for _chunk in r.iter_raw():
                break                                   # client walks away mid-stream
    deadline = time.time() + 10
    while (srv.hy.state.inflight("unc@rig") or rig.inflight) and time.time() < deadline:
        time.sleep(0.05)
    assert srv.hy.state.inflight("unc@rig") == 0 and srv.hy.state.node_inflight("rig") == 0
    deadline = time.time() + 10
    while rig.disconnects < 90 and time.time() < deadline:
        time.sleep(0.05)
    assert rig.disconnects >= 90, "the engine must see the disconnects (slot freed)"
    assert srv.hy.state.health["unc@rig"].h.fails == 0, "a client disconnect is not the node's fault"
    assert post(srv, chat(model="solo")).status_code == 200


# ───────────────────────────── instinct (reconciliation) ─────────────────────────────

class FakeInstinct:
    """A stdlib instinct-route/1 server: /contract, /route, /feedback."""

    def __init__(self, key: str, answer: dict, contracts=("instinct/1", "instinct-route/1")):
        import http.server
        self.key, self.answer, self.contracts = key, answer, list(contracts)
        self.routes, self.feedback = [], []
        me = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _j(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _ok(self):
                return self.headers.get("Authorization") == f"Bearer {me.key}"

            def do_GET(self):  # noqa: N802
                if not self._ok():
                    return self._j(401, {})
                self._j(200, {"contracts": me.contracts, "service_version": "0.1.0"})

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                if not self._ok():
                    return self._j(401, {})
                if self.path == "/v1/instinct/route":
                    me.routes.append(body)
                    return self._j(200, dict(me.answer, request_id=body["request_id"]))
                me.feedback.append(body)
                self._j(200, {"ok": True})

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _answer(action="act", enforce=False, label="bulk"):
    return {"contract": "instinct-route/1", "trace_id": "ins_T1", "action": action, "enforce": enforce,
            "mode": "enforce" if enforce else "shadow",
            "task_class": {"label": label, "probabilities": {label: 0.9}, "calibrated": True,
                           "mechanical": False, "decision_hash": "h"}, "pool_fit": None, "reason": None,
            "latency_ms": 1.0}


def _with_instinct(url, key_file):
    def mut(raw):
        raw["hydra"]["instinct"] = {"url": url, "key_file": str(key_file), "deadline_ms": 500}
        raw["rules"].append({"name": "bulk-fast", "when": {"task_class": "bulk", "model": "beast"},
                             "then": {"route": "beast:fast"}})
    return mut


def _key(tmp_path) -> Path:
    k = tmp_path / "instinct.key"
    k.write_text("ins-key\n")
    k.chmod(0o600)
    return k


def test_instinct_absent_is_static_policy(fleet, tmp_path):
    so = socket.socket()
    so.bind(("127.0.0.1", 0))
    dead = so.getsockname()[1]
    so.close()
    srv, rig, _, _ = fleet(_with_instinct(f"http://127.0.0.1:{dead}", _key(tmp_path)))
    for _ in range(3):
        r = post(srv, chat())
        assert r.status_code == 200 and r.headers["x-hydra-deployment"] == "unc@rig"
    assert audit_rows(tmp_path)[-1]["instinct"] is None or not audit_rows(tmp_path)[-1]["instinct"]["applied"]


def test_instinct_shadow_is_logged_not_applied(fleet, tmp_path):
    ins = FakeInstinct("ins-key", _answer(enforce=False))
    try:
        srv, rig, sparks, _ = fleet(_with_instinct(ins.url, _key(tmp_path)))
        r = post(srv, chat(content=SENTINEL))
        assert r.headers["x-hydra-route"] == "beast" and r.headers["x-hydra-deployment"] == "unc@rig"
        row = audit_rows(tmp_path)[-1]
        assert row["instinct"]["applied"] is False and row["instinct"]["label"] == "bulk"
        assert row["instinct"]["shadow_route"] == "beast:fast"
        assert row["instinct"]["shadow_deployment"] == "nvfp4@sparks"
        req = ins.routes[-1]
        assert req["contract"] == "instinct-route/1" and req["pools"] is None
        assert set(req["features"]) == {"prompt_head", "est_prompt_tokens", "has_images", "has_tools",
                                        "stream", "client_class"}
        deadline = time.time() + 3
        while not ins.feedback and time.time() < deadline:
            time.sleep(0.05)
        fb = ins.feedback[-1]
        # instinct's FeedbackReq is strict: served_pool/ttft_ms/error are top-level and
        # outcome is only {source, label?, signal?, weight?} (plan §5.10, §5.11 item 7)
        assert fb["trace_id"] == "ins_T1" and fb["served_pool"] == "unc@rig"
        assert fb["outcome"] == {"source": "hydra", "label": "ok"} and "error" not in fb
        assert set(fb) <= {"trace_id", "request_id", "outcome", "served_pool", "ttft_ms", "error"}
        assert fb["request_id"] == audit_rows(tmp_path)[-1]["request_id"]
    finally:
        ins.close()


_TASK_ROWS = [
    ("fix the bug in this function and run the tests", True, "agent", "code_agent"),
    ("refactor the parser module, compile, test", True, "agent", "code_agent"),
    ("please fix the failing test in utils", True, "agent", "code_agent"),
    ("hello, how are you today?", False, "interactive", "chat"),
    ("what is the capital of france", False, "interactive", "chat"),
    ("tell me a joke about cats", False, "interactive", "chat"),
    ("classify these 500 rows", False, "batch", "bulk"),
    ("summarize each document in this batch", False, "batch", "bulk"),
]


@pytest.fixture
def real_instinct(tmp_path):
    """The REAL instinct service (agents/instinct/server.py) on an ephemeral
    loopback port — a hand-written fake accepts any body, the real one's
    request models are strict, and only the real one catches contract drift."""
    import _instinct_helpers as H
    from instinct.config import load_config
    from instinct.server import create_app
    from instinct.service import Instinct
    rows = [{"input": {"prompt_head": t, "est_prompt_tokens": 500, "has_images": False,
                       "has_tools": tools, "stream": True, "client_class": cc}, "label": y}
            for t, tools, cc, y in _TASK_ROWS]
    home = tmp_path / "instinct"
    home.mkdir()
    cfgp, _ = H.promote_linear(home, "hydra.task_class", rows=rows, chain='["linear", "rules"]',
                               mode="shadow")
    inst = Instinct(load_config(cfgp, env={}), repo_root=home)
    key = (home / "instinct.key").read_text().strip()
    app = create_app(inst, key, allowed_hosts=["127.0.0.1"], probe_loop=False)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    th = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    th.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline, "instinct did not start"
        time.sleep(0.02)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}", home / "instinct.key", home / "ledger"
    server.should_exit = True
    th.join(timeout=10)


def _feedback_rows(ledger: Path) -> list[dict]:
    return [json.loads(line) for p in sorted(ledger.glob("feedback-*.jsonl"))
            for line in p.read_text().splitlines() if line.strip()]


def test_feedback_is_accepted_by_the_real_instinct(fleet, tmp_path, real_instinct):
    """Reconciliation §5 'feedback from day one': hydra -> real instinct, end to end."""
    url, key_file, ledger = real_instinct
    srv, _, _, _ = fleet(_with_instinct(url, key_file))
    r = post(srv, chat(content="fix the bug in this function and run the tests"))
    assert r.status_code == 200
    row = audit_rows(tmp_path)[-1]
    assert row["instinct"] and row["instinct"]["trace_id"], row["instinct"]
    deadline = time.time() + 5
    while not _feedback_rows(ledger) and time.time() < deadline:
        time.sleep(0.05)
    fb = _feedback_rows(ledger)
    assert len(fb) == 1, (fb, srv.hy.instinct.feedback_result)
    assert fb[0]["trace_id"] == row["instinct"]["trace_id"]
    assert fb[0]["served_pool"] == "unc@rig" and fb[0]["outcome"] == {"source": "hydra", "label": "ok"}
    assert srv.hy.instinct.feedback_result == {"ok": 1}
    m = httpx.get(srv.url + "/hydra/metrics", headers=srv.local()).text
    assert 'hydra_instinct_feedback_total{result="ok"} 1' in m


def test_feedback_rejection_is_counted_not_silent(tmp_path, real_instinct):
    """A non-200 from instinct is a failure hydra counts and says once — never 'sent'."""
    import asyncio
    url, key_file, _ = real_instinct
    ic = hydra.InstinctClient(core.InstinctCfg(url=url, key_file=str(key_file)), REPO)

    async def go():
        try:
            good = hydra.InstinctClient.feedback_body("ins_x", "req-1", served_pool="unc@rig",
                                                      ttft_ms=12.5, outcome="upstream_failed")
            assert good["error"] == "upstream_failed"
            ok = await ic.feedback(good)
            bad = await ic.feedback({"trace_id": "ins_y", "outcome": {"source": "hydra", "status": 200}})
            return ok, bad
        finally:
            await ic.aclose()
    ok, bad = asyncio.run(go())
    assert ok is True and bad is False
    assert ic.feedback_result == {"ok": 1, "http_400": 1} and ic.feedback_warned


def test_instinct_act_and_enforce_is_applied(fleet, tmp_path):
    ins = FakeInstinct("ins-key", _answer(enforce=True))
    try:
        srv, _, _, _ = fleet(_with_instinct(ins.url, _key(tmp_path)))
        r = post(srv, chat())
        assert r.headers["x-hydra-route"] == "beast:fast" and "bulk-fast" in r.headers["x-hydra-rule"]
        assert audit_rows(tmp_path)[-1]["instinct"]["applied"] is True
        # enforce without act (abstain) is never applied
        ins.answer = _answer(action="abstain", enforce=True)
        assert post(srv, chat()).headers["x-hydra-route"] == "beast"
        # a pin is never sent to instinct at all
        n = len(ins.routes)
        post(srv, chat(model="unc@rig"))
        assert len(ins.routes) == n
    finally:
        ins.close()


def test_instinct_without_the_contract_is_not_asked(fleet, tmp_path):
    ins = FakeInstinct("ins-key", _answer(enforce=True), contracts=("instinct/1",))
    try:
        srv, _, _, _ = fleet(_with_instinct(ins.url, _key(tmp_path)))
        assert post(srv, chat()).headers["x-hydra-route"] == "beast"
        assert ins.routes == []
    finally:
        ins.close()


def test_verify_upstream_false_skips_the_mismatch_check(fleet):
    def mut(raw):
        raw["deployments"]["unc@rig"].update(upstream="not-what-llama-lists", verify_upstream=False)
    srv, rig, _, _ = fleet(mut)
    srv.hy.next_models.clear()
    time.sleep(1.5)                           # at least one more /v1/models check
    assert srv.hy.state.health["unc@rig"].h.state == core.READY
    r = post(srv, chat(model="unc@rig"))
    assert r.status_code == 200 and posts(rig)[-1]["body"]["model"] == "not-what-llama-lists"


def test_a_known_served_id_that_vanishes_is_mismatch(fleet):
    srv, rig, _, _ = fleet()
    rig.set_fault("wrong_model")              # the rig was relaunched with another model
    srv.hy.next_models.clear()
    wait_ready(srv, ["unc@rig"], core.MISMATCH)
    assert post(srv, chat(model="unc@rig")).status_code == 503


# ───────────────────────────── fault injection (review 2026-09-30) ─────────────────────────────

def _rename(srv, tmp_path, old, new):
    raw = copy.deepcopy(srv.raw)
    raw["deployments"][new] = raw["deployments"].pop(old)
    for r in raw["routes"].values():
        for tg in r["targets"]:
            if tg["d"] == old:
                tg["d"] = new
    (tmp_path / "hydra.toml").write_text(core.to_toml(raw))
    assert httpx.post(srv.url + "/hydra/reload", headers=srv.local()).json()["ok"]
    assert old not in srv.hy.state.health


def test_a_reload_that_renames_the_deployment_mid_request_does_not_leak(fleet, tmp_path):
    srv, rig, sparks, _ = fleet(cfg_file=True)
    got = {}

    def slow():
        r = post(srv, chat(model="solo"), headers={"X-Fake-Fault": "ttft_ms:1500"})
        got["r"] = (r.status_code, r.headers.get("x-hydra-deployment"))
    t = threading.Thread(target=slow)
    t.start()
    time.sleep(0.4)
    _rename(srv, tmp_path, "unc@rig", "unc2@rig")
    t.join(15)
    assert got["r"] == (200, "unc@rig"), got
    assert srv.hy.state.node_inflight("rig") == 0, "a reload leaked the rig's only slot"
    assert audit_rows(tmp_path)[-1]["outcome"] == "ok"


def test_a_failover_target_renamed_mid_request_is_still_tried_on_the_old_snapshot(fleet, tmp_path):
    srv, rig, sparks, _ = fleet(lambda r: r["nodes"]["rig"].update(ttft_timeout_s=1), cfg_file=True)
    got = {}

    def slow():
        r = post(srv, chat(model="retry", stream=True), headers={"X-Fake-Fault": "ttft_ms:1500"})
        got["r"] = (r.status_code, r.headers.get("x-hydra-deployment"), r.headers.get("x-hydra-attempts"))
    t = threading.Thread(target=slow)
    t.start()
    time.sleep(0.4)
    _rename(srv, tmp_path, "nvfp4@sparks", "nvfp4b@sparks")
    t.join(15)
    assert got["r"][:2] == (200, "nvfp4@sparks"), got
    assert srv.hy.state.node_inflight("sparks") == 0 and srv.hy.state.node_inflight("rig") == 0


def test_a_half_open_trial_is_exclusive_across_failover(fleet):
    # A holds the 1-slot rig and times out into sparks; B has meanwhile spilled
    # to sparks as the single HALF_OPEN trial. A's failover (planned before B
    # took the trial) must not become a second trial.
    srv, rig, sparks, _ = fleet(lambda r: r["nodes"]["rig"].update(ttft_timeout_s=1))
    hs = srv.hy.state.health["nvfp4@sparks"]
    hs.h.breaker, hs.h.opened_at = core.OPEN, time.monotonic() - 1000       # due for HALF_OPEN
    seen = {"trial": 0}
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            seen["trial"] = max(seen["trial"], hs.h.trial_inflight)
            time.sleep(0.005)
    out = {}

    def req(name):
        r = post(srv, chat(model="retry", stream=True), headers={"X-Fake-Fault": "ttft_ms:2000"})
        out[name] = (r.status_code, r.headers.get("x-hydra-attempts"))
    w = threading.Thread(target=watch)
    w.start()
    a = threading.Thread(target=req, args=("a",))
    a.start()
    time.sleep(0.3)
    b = threading.Thread(target=req, args=("b",))
    b.start()
    a.join(20)
    b.join(20)
    stop.set()
    w.join()
    assert out["b"][1].startswith("nvfp4@sparks:"), out
    assert "nvfp4@sparks:skipped" in out["a"][1], out
    assert seen["trial"] <= 1 and sparks.max_inflight_seen <= 1, (seen, sparks.max_inflight_seen)
    assert srv.hy.state.inflight("nvfp4@sparks") == 0 and hs.h.trial_inflight == 0


@pytest.mark.parametrize("stream", [True, False])
def test_a_stalled_error_body_honours_the_deadline_and_fails_over(fleet, stream):
    # 500 headers + 5 of 100 body bytes, then silence: without a deadline on
    # the error-body read the attempt hangs the full stall and never fails over.
    srv, rig, sparks, _ = fleet(lambda r: r["nodes"]["rig"].update(ttft_timeout_s=1, nonstream_timeout_s=1))
    rig.set_fault("error_body_stall:8000", 1)
    t0 = time.time()
    r = post(srv, chat(stream=stream))
    took = time.time() - t0
    assert r.status_code == 200 and r.headers["x-hydra-deployment"] == "nvfp4@sparks", r.text
    assert r.headers["x-hydra-attempts"].split(",")[0] == "unc@rig:500"
    assert took < 5, f"hydra sat {took:.1f}s on a stalled error body (deadline 1s)"
    assert srv.hy.state.health["unc@rig"].h.fails == 1
    assert srv.hy.state.node_inflight("rig") == 0


@pytest.mark.parametrize("stream", [True, False])
def test_a_clean_empty_2xx_fails_over_instead_of_committing(fleet, stream, tmp_path):
    srv, rig, sparks, _ = fleet()
    rig.set_fault("empty_200", 1)
    r = post(srv, chat(stream=stream))
    assert r.status_code == 200 and r.headers["x-hydra-deployment"] == "nvfp4@sparks"
    assert r.content, "never an empty answer"
    assert r.headers["x-hydra-attempts"] == "unc@rig:empty,nvfp4@sparks:200"
    h = srv.hy.state.health["unc@rig"].h
    assert h.fails == 1 and h.served_total == 0, "an empty 2xx is a failure, not a success"


@pytest.mark.parametrize("stream", [True, False])
def test_a_caller_that_leaves_before_the_commit_point_is_released(fleet, stream, tmp_path):
    srv, rig, _, _ = fleet()
    with pytest.raises(httpx.TimeoutException):
        httpx.post(srv.url + "/v1/chat/completions", json=chat(model="solo", stream=stream),
                   headers=auth({"X-Fake-Fault": "ttft_ms:3000"}), timeout=0.5)
    deadline = time.time() + 1.5
    while srv.hy.state.node_inflight("rig") and time.time() < deadline:
        time.sleep(0.05)
    assert srv.hy.state.node_inflight("rig") == 0, "hydra held the slot for a caller that had left"
    while (rig.inflight or not rig.disconnects) and time.time() < deadline + 1:
        time.sleep(0.05)
    assert rig.disconnects >= 1 and not rig.inflight, "the ENGINE must see the hang-up (plan §6.6)"
    row = audit_rows(tmp_path)[-1]
    assert row["outcome"] == "client_disconnect" and row["status"] == 499, row
    assert srv.hy.state.health["unc@rig"].h.fails == 0, "a caller leaving is not the node's fault"


def test_an_oversized_nonstream_body_that_fails_midway_is_not_a_clean_eof(fleet, tmp_path, monkeypatch):
    monkeypatch.setattr(hydra, "NONSTREAM_BUFFER", 64)      # commit and relay past 64 bytes
    srv, rig, _, _ = fleet()
    with pytest.raises(httpx.HTTPError):
        post(srv, chat(model="solo"), {"X-Fake-Fault": "body_then_close:4000"})
    time.sleep(0.3)
    row = audit_rows(tmp_path)[-1]
    assert row["outcome"] == "upstream_failed_midstream", row
    assert srv.hy.state.node_inflight("rig") == 0


def test_an_upstream_cannot_forge_provenance_headers(fleet):
    srv, rig, _, _ = fleet()
    r = post(srv, chat(), {"X-Fake-Fault": "forge_headers"})
    assert r.status_code == 200
    assert r.headers.get_list("x-hydra-deployment") == ["unc@rig"]
    assert r.headers.get_list("x-hydra-rule") == [], "no rule fired: no rule header, forged or not"
    assert all(len(r.headers.get_list(k)) == 1 for k in r.headers if k.lower().startswith("x-hydra-"))


def test_tensorfold_never_gets_a_key_even_past_validation(fleet):
    # validate() refuses a key on a TensorFold node; this is the runtime guard
    # behind it (a node edited after validation, or a validator regression).
    import dataclasses
    srv, rig, _, tf = fleet()
    n = dataclasses.replace(srv.hy.cfg.nodes["tf"], key_env="RIG_KEY")
    srv.hy.cfg.nodes["tf"] = n
    assert srv.hy.node_key(n) is None
    r = post(srv, chat(model="beast:tf"))
    assert r.status_code == 200 and r.headers["x-hydra-deployment"] == "mlx@tf"
    assert "authorization" not in posts(tf)[-1]["headers"]
