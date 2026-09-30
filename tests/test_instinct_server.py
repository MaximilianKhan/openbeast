#!/usr/bin/env python3
"""The HTTP service: auth, status semantics, fallback-as-200, ledger rows,
privacy (plan §5.13 test_instinct_server)."""
from __future__ import annotations

import contextlib
import json
import os
import stat

import pytest
from starlette.testclient import TestClient

import _instinct_helpers as H
from instinct.config import load_config
from instinct.server import create_app, read_service_key
from instinct.service import Instinct

KEY = "k" * 40
AUTH = {"Authorization": f"Bearer {KEY}"}
SECRET = "the-quick-secret-marmot-7731"


def body(text="hello", **kw):
    b = {"contract": "instinct/1", "decision": "router.spawn_intent",
         "inputs": {"user_turn": text}}
    b.update(kw)
    return b


@contextlib.contextmanager
def service(tmp_path, url=None, *, log_inputs="hash", call_log=None, faults=None,
            decisions=("router.spawn_intent",), debug=False):
    stub_ctx = H.stub_server(faults or {}, call_log) if url is None else contextlib.nullcontext(
        (url, None))
    with stub_ctx as (u, _):
        cfgp = H.write_config(tmp_path, {"stub": H.llama_binding(u)}, decisions=list(decisions),
                              service={"log_inputs": log_inputs})
        cfg = load_config(cfgp, env={"INSTINCT_ENGINE_OVERRIDE": "stub"})
        inst = Instinct(cfg)
        app = create_app(inst, KEY, allowed_hosts=["testserver", "127.0.0.1"],
                         debug_score=debug, probe_loop=False)
        with TestClient(app) as c:
            yield c, cfg


def ledger_rows(cfg):
    rows = []
    for p in sorted(cfg.ledger_dir.glob("decisions-*.jsonl")):
        rows += [json.loads(line) for line in p.read_text().splitlines()]
    return rows


def test_auth(tmp_path):
    with service(tmp_path) as (c, _):
        assert c.get("/health").json() == {"ok": True}                      # open
        assert c.post("/v1/instinct/decide", json=body()).status_code == 401
        assert c.post("/v1/instinct/decide", json=body(),
                      headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.get("/v1/instinct/decisions").status_code == 401
        assert c.get("/metrics").status_code == 401
        assert c.post("/v1/instinct/decide", json=body(), headers=AUTH).status_code == 200


def test_host_header_pinned(tmp_path):
    with service(tmp_path) as (c, _):
        assert c.get("/health", headers={"Host": "evil.example"}).status_code == 400
        assert c.get("/health", headers={"Host": "127.0.0.1"}).status_code == 200


@pytest.mark.parametrize("payload,code", [
    ("not json", 400),
    (body(bogus=1), 400),                                   # extra=forbid
    (body(contract="instinct/2"), 400),
    (body(ceiling="yolo"), 400),
    (body(decision="nope.nothing"), 404),
    ({"contract": "instinct/1", "decision": "router.spawn_intent", "inputs": {}}, 422),
    ({"contract": "instinct/1", "decision": "router.spawn_intent",
      "inputs": {"user_turn": 5}}, 422),
    ({"contract": "instinct/1", "decision": "router.spawn_intent",
      "inputs": {"user_turn": "x", "extra": "y"}}, 422),
    (body(items=[{"id": "a", "text": "b"}]), 422),          # items on a non-rank decision
])
def test_status_semantics(tmp_path, payload, code):
    with service(tmp_path) as (c, _):
        if isinstance(payload, str):
            r = c.post("/v1/instinct/decide", content=payload, headers=AUTH)
        else:
            r = c.post("/v1/instinct/decide", json=payload, headers=AUTH)
        assert r.status_code == code, r.text


def test_engine_down_is_200_fallback(tmp_path):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = f"http://127.0.0.1:{s.getsockname()[1]}"
    s.close()
    with service(tmp_path, url=dead) as (c, cfg):
        r = c.post("/v1/instinct/decide", json=body("spawn an agent"), headers=AUTH)
        assert r.status_code == 200
        j = r.json()
        assert j["enforce"] is False
        stub = next(x for x in j["cascade"] if x["engine"] == "stub")
        assert stub["action"] == "skipped"
        # every engine unusable except rules -> rules answers, never enforce
        assert j["engine"]["id"] == "rules"


def test_shadow_populates_would_and_never_enforces(tmp_path):
    with service(tmp_path) as (c, _):
        j = c.post("/v1/instinct/decide", json=body("what is 2+2"), headers=AUTH).json()
    assert j["mode"] == "shadow" and j["enforce"] is False
    assert j["would"]["label"] in ("inline", "spawn")
    assert j["fallback"]["used"] is True


def test_one_ledger_row_per_call_including_fallbacks(tmp_path):
    with service(tmp_path) as (c, cfg):
        c.post("/v1/instinct/decide", json=body("a"), headers=AUTH)
        c.post("/v1/instinct/decide", json=body("b", context={"eval": True}), headers=AUTH)
        c.post("/v1/instinct/decide", json=body("c", ceiling="off"), headers=AUTH)
        c.post("/v1/instinct/route", headers=AUTH, json={
            "contract": "instinct-route/1", "features": {
                "prompt_head": "x", "est_prompt_tokens": 1, "has_images": False,
                "has_tools": False, "stream": True, "client_class": "agent"}})
        c.post("/v1/instinct/decide", json=body(decision="nope.x"), headers=AUTH)   # 404
        rows = ledger_rows(cfg)
    assert len(rows) == 4          # 3 decides + 1 route (hydra.task_class not registered)
    assert [r["action"] for r in rows[:2]][1] == "fallback"
    assert rows[1]["fallback_reason"] == "eval_context"
    for p in cfg.ledger_dir.glob("decisions-*.jsonl"):
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_eval_context_never_calls_an_engine(tmp_path):
    log = tmp_path / "calls.jsonl"
    with service(tmp_path, call_log=str(log)) as (c, _):
        before = len(H.read_calls(log))
        j = c.post("/v1/instinct/decide", json=body("spawn it", context={"eval": True}),
                   headers=AUTH).json()
        after = len(H.read_calls(log))
    assert j["action"] == "fallback" and j["fallback"]["reason"] == "eval_context"
    assert j["enforce"] is False and before == after


def test_hash_privacy_never_writes_text(tmp_path):
    with service(tmp_path, log_inputs="hash") as (c, cfg):
        c.post("/v1/instinct/decide", json=body(SECRET), headers=AUTH)
        blob = "".join(p.read_text() for p in cfg.ledger_dir.glob("*.jsonl"))
    assert SECRET not in blob and "input_sha256" in blob


def test_excerpt_privacy_writes_text(tmp_path):
    # control for the test above; the spec's own privacy.log_inputs wins over
    # the service default, so flip the spec
    text = H.spec_text("router.spawn_intent").replace('log_inputs = "hash"',
                                                      'log_inputs = "excerpt"')
    with H.stub_server() as (u, _):
        cfgp = H.write_config(tmp_path, {"stub": H.llama_binding(u)},
                              extra_decisions={"router.spawn_intent": text})
        cfg = load_config(cfgp, env={"INSTINCT_ENGINE_OVERRIDE": "stub"})
        app = create_app(Instinct(cfg), KEY, allowed_hosts=["testserver"], probe_loop=False)
        with TestClient(app) as c:
            c.post("/v1/instinct/decide", json=body(SECRET), headers=AUTH)
        blob = "".join(p.read_text() for p in cfg.ledger_dir.glob("*.jsonl"))
    assert SECRET in blob


def test_views_metrics_contract(tmp_path):
    with service(tmp_path) as (c, _):
        c.post("/v1/instinct/decide", json=body(), headers=AUTH)
        d = c.get("/v1/instinct/decisions", headers=AUTH).json()
        assert d["decisions"][0]["id"] == "router.spawn_intent"
        assert {e["engine"] for e in d["decisions"][0]["engines"]} == {"linear", "stub", "rules"}
        e = c.get("/v1/instinct/engines", headers=AUTH).json()
        assert any(x["id"] == "stub" and x["healthy"] for x in e["engines"])
        m = c.get("/metrics", headers=AUTH).text
        assert "instinct_decisions_total{" in m and "instinct_engine_up{" in m
        assert c.get("/v1/instinct/contract", headers=AUTH).json()["contracts"] == [
            "instinct/1", "instinct-route/1"]
        st = c.get("/v1/instinct/stats", headers=AUTH).json()["stats"]
        assert st["router.spawn_intent"]["window"] == 1


def test_debug_score_off_by_default(tmp_path):
    req = {"engine": "stub", "query": "hi", "items": [], "labels": {"a": 1}}
    with service(tmp_path) as (c, _):
        assert c.post("/v1/instinct/score", json=req, headers=AUTH).status_code == 404
    with service(tmp_path, debug=True) as (c, _):
        r = c.post("/v1/instinct/score", json=req, headers=AUTH)
        assert r.status_code == 200 and "rows" in r.json()


def test_feedback_is_append_only(tmp_path):
    with service(tmp_path) as (c, cfg):
        r = c.post("/v1/instinct/feedback", headers=AUTH,
                   json={"trace_id": "ins_x", "served_pool": "spark", "ttft_ms": 12.5,
                         "outcome": {"source": "hydra", "signal": 1.0}})
        assert r.json() == {"ok": True}
        assert c.post("/v1/instinct/feedback", headers=AUTH,
                      json={"trace_id": "x", "nope": 1}).status_code == 400
    rows = (cfg.ledger_dir / "feedback.jsonl").read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["served_pool"] == "spark"


def test_registry_invalid_decision_is_200_fallback(tmp_path):
    bad = H.spec_text("router.spawn_intent").replace('version     = 1', 'version     = 0')
    with H.stub_server() as (u, _):
        cfgp = H.write_config(tmp_path, {"stub": H.llama_binding(u)},
                              extra_decisions={"router.spawn_intent": bad})
        app = create_app(Instinct(load_config(cfgp, env={})), KEY,
                         allowed_hosts=["testserver"], probe_loop=False)
        with TestClient(app) as c:
            j = c.post("/v1/instinct/decide", json=body(), headers=AUTH).json()
    assert j["action"] == "fallback" and j["fallback"]["reason"] == "registry_invalid"


def test_service_key_fails_closed(tmp_path):
    kf = tmp_path / "k"
    with pytest.raises(OSError):
        read_service_key(kf)                                  # missing
    kf.write_text("x" * 40)
    os.chmod(kf, 0o644)
    with pytest.raises(PermissionError):
        read_service_key(kf)                                  # world-readable
    os.chmod(kf, 0o600)
    assert read_service_key(kf) == "x" * 40                    # control
    kf.write_text("short")
    with pytest.raises(PermissionError):
        read_service_key(kf)
    with pytest.raises(PermissionError):
        create_app(Instinct(load_config(H.write_config(tmp_path, {}), env={})), "")
