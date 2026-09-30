#!/usr/bin/env python3
"""Freezes the instinct/1 and instinct-route/1 wire shapes (like
test_beast_slot.py freezes /api/slot) and exercises a mock hydra against the
real service (plan §5.11, §5.13 test_instinct_contract).

A change here is a CONTRACT change: v1 fields never change meaning; anything
breaking becomes instinct-route/2, served in parallel for one release."""
from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
from starlette.testclient import TestClient

import _instinct_helpers as H
from instinct.client import InstinctClient
from instinct.config import load_config
from instinct.server import create_app
from instinct.service import CONTRACTS, Instinct

import mock_hydra as MH

KEY = "k" * 40
AUTH = {"Authorization": f"Bearer {KEY}"}

DECIDE_KEYS = {"contract", "decision", "decision_version", "decision_hash", "trace_id",
               "request_id", "mode", "enforce", "action", "answer", "items", "would",
               "fallback", "engine", "cascade", "latency_ms"}
ANSWER_KEYS = {"type", "label", "probabilities", "raw_probabilities", "calibrated",
               "confidence", "label_mass", "labels_truncated", "expected_value"}
ENGINE_KEYS = {"id", "adapter", "model", "model_sha256", "exec", "label_token_ids"}
ROUTE_KEYS = {"contract", "request_id", "trace_id", "action", "enforce", "mode",
              "task_class", "pool_fit", "reason", "latency_ms"}
TASK_CLASS_KEYS = {"label", "probabilities", "calibrated", "mechanical", "decision_hash"}
FEATURES = {"prompt_head": "please fix the bug in this function", "est_prompt_tokens": 900,
            "has_images": False, "has_tools": True, "stream": True, "client_class": "agent"}

TASK_ROWS = [
    ({"prompt_head": "fix the bug in this function and run the tests", "has_tools": True,
      "client_class": "agent"}, "code_agent"),
    ({"prompt_head": "refactor the parser module, compile, test", "has_tools": True,
      "client_class": "agent"}, "code_agent"),
    ({"prompt_head": "please fix the failing test in utils", "has_tools": True,
      "client_class": "agent"}, "code_agent"),
    ({"prompt_head": "hello, how are you today?", "has_tools": False,
      "client_class": "interactive"}, "chat"),
    ({"prompt_head": "what is the capital of france", "has_tools": False,
      "client_class": "interactive"}, "chat"),
    ({"prompt_head": "tell me a joke about cats", "has_tools": False,
      "client_class": "interactive"}, "chat"),
    ({"prompt_head": "classify these 500 rows", "has_tools": False, "stream": False,
      "client_class": "batch"}, "bulk"),
    ({"prompt_head": "summarize each document in this batch", "has_tools": False,
      "stream": False, "client_class": "batch"}, "bulk"),
]


def task_rows():
    out = []
    for facts, y in TASK_ROWS:
        inp = {"prompt_head": "", "est_prompt_tokens": 500, "has_images": False,
               "has_tools": False, "stream": True, "client_class": "interactive"}
        inp.update(facts)
        out.append({"input": inp, "label": y})
    return out


def app_for(cfgp, env=None):
    inst = Instinct(load_config(cfgp, env=env or {}), repo_root=cfgp.parent)
    return inst, create_app(inst, KEY, allowed_hosts=["testserver", "instinct.test"],
                            probe_loop=False)


def test_contract_list_is_frozen():
    assert CONTRACTS == ["instinct/1", "instinct-route/1"]


def test_decide_shape(tmp_path):
    cfgp, _ = H.promote_linear(tmp_path)
    _, app = app_for(cfgp)
    with TestClient(app) as c:
        j = c.post("/v1/instinct/decide", headers=AUTH, json={
            "contract": "instinct/1", "decision": "router.spawn_intent",
            "request_id": "c0ffee", "inputs": {"user_turn": "what does this function do"},
            "items": None, "baseline": "hint", "ceiling": "enforce", "deadline_ms": 600,
            "context": {"caller": "router", "eval": False}}).json()
    assert set(j) == DECIDE_KEYS
    assert set(j["answer"]) == ANSWER_KEYS
    assert set(j["engine"]) == ENGINE_KEYS
    assert set(j["fallback"]) == {"used", "reason"}
    assert set(j["would"]) == {"label", "action"}
    assert set(j["latency_ms"]) == {"queue", "engine", "total"}
    assert set(j["answer"]["confidence"]) == {"p_top", "margin", "shape"}
    assert j["contract"] == "instinct/1" and j["request_id"] == "c0ffee"
    assert isinstance(j["enforce"], bool) and j["action"] in ("act", "review", "abstain",
                                                              "fallback")
    assert j["mode"] in ("off", "shadow", "canary", "enforce")
    assert j["trace_id"].startswith("ins_") and len(j["decision_hash"]) == 64
    for entry in j["cascade"]:
        assert {"engine", "action", "ms"} <= set(entry)


def test_unknown_request_fields_rejected(tmp_path):
    cfgp, _ = H.promote_linear(tmp_path)
    _, app = app_for(cfgp)
    with TestClient(app) as c:
        base = {"contract": "instinct/1", "decision": "router.spawn_intent",
                "inputs": {"user_turn": "x"}}
        assert c.post("/v1/instinct/decide", headers=AUTH, json=base).status_code == 200
        assert c.post("/v1/instinct/decide", headers=AUTH,
                      json={**base, "mode": "enforce"}).status_code == 400   # no caller mode
        assert c.post("/v1/instinct/decide", headers=AUTH,
                      json={**base, "context": {"eval": False, "x": 1}}).status_code == 400
        r = {"contract": "instinct-route/1", "features": FEATURES}
        assert c.post("/v1/instinct/route", headers=AUTH, json=r).status_code == 200
        assert c.post("/v1/instinct/route", headers=AUTH,
                      json={**r, "load": {"spark": 0.9}}).status_code == 400  # hydra never sends load
        assert c.post("/v1/instinct/route", headers=AUTH, json={
            **r, "features": {**FEATURES, "gpu": "5090"}}).status_code == 400


def test_route_shape_and_mechanical(tmp_path):
    cfgp, _ = H.promote_linear(tmp_path, "hydra.task_class", rows=task_rows(),
                               chain='["linear", "rules"]')
    _, app = app_for(cfgp)
    with TestClient(app) as c:
        j = c.post("/v1/instinct/route", headers=AUTH, json={
            "contract": "instinct-route/1", "request_id": "r1", "deadline_ms": 25,
            "features": FEATURES, "pools": None}).json()
        assert set(j) == ROUTE_KEYS and set(j["task_class"]) == TASK_CLASS_KEYS
        assert j["contract"] == "instinct-route/1" and j["pool_fit"] is None
        assert j["task_class"]["label"] == "code_agent" and j["enforce"] is True
        assert j["task_class"]["mechanical"] is False
        # vision is mechanical: computed, never judged — and rules never enforce (I6)
        v = c.post("/v1/instinct/route", headers=AUTH, json={
            "contract": "instinct-route/1",
            "features": {**FEATURES, "has_images": True}}).json()
        assert v["task_class"]["label"] == "vision" and v["task_class"]["mechanical"] is True
        assert v["enforce"] is False
        # a model can never answer a mechanical label when the facts do not hold
        probs = j["task_class"]["probabilities"]
        assert probs["vision"] == 0.0 and probs["long_context"] == 0.0


def _asgi_client(app, tmp_path):
    kf = tmp_path / "client.key"
    kf.write_text(KEY)
    kf.chmod(0o600)
    return InstinctClient("http://instinct.test", kf, transport=httpx.ASGITransport(app=app))


POOLS = [MH.PoolDesc("rig-5090", ctx_max=262144, vision=True, load=0.2),
         MH.PoolDesc("spark-1", ctx_max=131072, load=0.1),
         MH.PoolDesc("spark-2", ctx_max=131072, load=0.5)]
CLASS_MAP = {"code_agent": "rig-5090", "chat": "spark-1", "bulk": "spark-2"}


def test_mock_hydra_with_instinct_dead_uses_static_policy(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    kf = tmp_path / "k"
    kf.write_text(KEY)
    kf.chmod(0o600)
    hydra = MH.MockHydra(POOLS, CLASS_MAP, InstinctClient(f"http://127.0.0.1:{port}", kf))
    pick = asyncio.run(hydra.pick(FEATURES))
    assert pick.pool == "spark-1" and pick.why.startswith("static") and not pick.instinct_used
    # and with no instinct configured at all
    assert asyncio.run(MH.MockHydra(POOLS, CLASS_MAP, None).pick(FEATURES)).pool == "spark-1"


def test_mock_hydra_uses_an_enforced_task_class(tmp_path):
    cfgp, _ = H.promote_linear(tmp_path, "hydra.task_class", rows=task_rows(),
                               chain='["linear", "rules"]')
    inst, app = app_for(cfgp)

    async def go():
        await inst.start()
        hydra = MH.MockHydra(POOLS, CLASS_MAP, _asgi_client(app, tmp_path))
        a = await hydra.pick(FEATURES)
        # hard filters first: images -> only the vision pool is eligible, no /route call
        calls = hydra.route_calls
        b = await hydra.pick({**FEATURES, "has_images": True})
        return a, b, calls, hydra.route_calls
    a, b, calls_before, calls_after = asyncio.run(go())
    assert a.pool == "rig-5090" and a.instinct_used and a.trace_id.startswith("ins_")
    assert b.pool == "rig-5090" and calls_after == calls_before   # <2 eligible: no call


def test_mock_hydra_ignores_a_shadow_answer(tmp_path):
    cfgp, _ = H.promote_linear(tmp_path, "hydra.task_class", rows=task_rows(),
                               chain='["linear", "rules"]', mode="shadow")
    inst, app = app_for(cfgp)

    async def go():
        await inst.start()
        return await MH.MockHydra(POOLS, CLASS_MAP, _asgi_client(app, tmp_path)).pick(FEATURES)
    pick = asyncio.run(go())
    assert pick.pool == "spark-1" and "did not enforce" in pick.why


def test_mock_hydra_refuses_instinct_engine_pools():
    with pytest.raises(ValueError):
        MH.MockHydra(POOLS + [MH.PoolDesc("scorer", 8192, role="instinct-engine")], CLASS_MAP)
