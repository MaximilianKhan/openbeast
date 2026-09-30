#!/usr/bin/env python3
"""The fail-open client (plan §5.13 test_instinct_client; invariant I3):
every failure is a non-enforcing Verdict, fast, and gate() runs legacy."""
from __future__ import annotations

import asyncio
import os
import socket
import time

import httpx
import pytest

import _instinct_helpers as H  # noqa: F401
from instinct.client import BREAKER_FAILS, InstinctClient, Verdict, gate


def keyfile(tmp_path, mode=0o600):
    p = tmp_path / "instinct.key"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, b"k" * 40)
    os.close(fd)
    os.chmod(p, mode)
    return p


def ok_response(enforce=True, action="act", label="inline", items=None):
    return {"contract": "instinct/1", "enforce": enforce, "action": action,
            "answer": {"label": label}, "items": items, "trace_id": "ins_1",
            "fallback": {"used": not enforce, "reason": None if enforce else "lifecycle_shadow"}}


class Recorder:
    def __init__(self, handler):
        self.calls = []
        self.handler = handler

    async def __call__(self, request: httpx.Request):
        self.calls.append(request)
        return await self.handler(request)


def client(tmp_path, handler, clock=time.monotonic, key_mode=0o600):
    rec = Recorder(handler)
    c = InstinctClient("http://instinct.test", keyfile(tmp_path, key_mode), clock=clock,
                       transport=httpx.MockTransport(rec))
    return c, rec


def decide(c, **kw):
    return asyncio.run(c.decide("router.spawn_intent", {"user_turn": "x"}, deadline_ms=50,
                                **kw))


def test_enforcing_verdict_passes_through(tmp_path):
    async def h(req):
        assert req.headers["authorization"] == "Bearer " + "k" * 40
        return httpx.Response(200, json=ok_response())
    c, rec = client(tmp_path, h)
    v = decide(c)
    assert v.enforce and v.label == "inline" and v.action == "act"


def test_dead_service_fails_open_fast(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    c = InstinctClient(f"http://127.0.0.1:{port}", keyfile(tmp_path))
    t0 = time.perf_counter()
    v = decide(c)
    assert not v.enforce and v.reason == "client_error"
    assert time.perf_counter() - t0 < 0.5


@pytest.mark.parametrize("resp", [
    httpx.Response(500, text="boom"),
    httpx.Response(401, json={"error": "unauthorized"}),
    httpx.Response(404, json={"error": "unknown decision"}),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json=[1, 2]),
    httpx.Response(200, json={**ok_response(), "contract": "instinct/0"}),
    httpx.Response(200, json={**ok_response(), "enforce": "yes"}),
    httpx.Response(200, json={**ok_response(), "action": "launch"}),
])
def test_bad_answers_fail_open(tmp_path, resp):
    async def h(req):
        return resp
    c, _ = client(tmp_path, h)
    v = decide(c)
    assert v.enforce is False and v.action == "fallback"


def test_slow_service_respects_deadline(tmp_path):
    async def h(req):
        await asyncio.sleep(2)
        return httpx.Response(200, json=ok_response())
    c, _ = client(tmp_path, h)
    t0 = time.perf_counter()
    v = decide(c)
    dt = time.perf_counter() - t0
    assert v.enforce is False and dt < 0.3        # deadline 50 ms + 10 ms slack


def test_missing_or_open_key_means_permanent_fallback(tmp_path):
    async def h(req):
        return httpx.Response(200, json=ok_response())
    c, rec = client(tmp_path, h, key_mode=0o644)
    v = decide(c)
    assert v.reason == "client_no_key" and rec.calls == []
    os.unlink(c.key_file)
    assert decide(c).reason == "client_no_key" and rec.calls == []


def test_breaker_opens_after_5_and_half_opens_after_30s(tmp_path):
    now = [1000.0]

    async def h(req):
        return httpx.Response(500)
    c, rec = client(tmp_path, h, clock=lambda: now[0])
    for _ in range(BREAKER_FAILS):
        assert decide(c).reason == "client_error"
    assert len(rec.calls) == BREAKER_FAILS
    v = decide(c)
    assert v.reason == "client_breaker_open" and len(rec.calls) == BREAKER_FAILS
    now[0] += 29.0
    assert decide(c).reason == "client_breaker_open"
    now[0] += 2.0                                   # past 30 s: one trial goes out
    assert decide(c).reason == "client_error" and len(rec.calls) == BREAKER_FAILS + 1

    async def good(req):
        return httpx.Response(200, json=ok_response())
    rec.handler = good
    now[0] += 31.0
    assert decide(c).enforce is True                # closed again
    assert c.fails == 0


def test_rank_ids_outside_the_input_fail_open(tmp_path):
    """I1 on the client side: never trust an id we did not send."""
    async def h(req):
        return httpx.Response(200, json=ok_response(items=[{"id": "a", "p": 0.9},
                                                           {"id": "EVIL", "p": 0.99}]))
    c, _ = client(tmp_path, h)
    v = asyncio.run(c.decide("hydra.pool_fit", {"prompt_head": "x"}, deadline_ms=50,
                             items=[{"id": "a", "text": "A"}, {"id": "b", "text": "B"}]))
    assert v.enforce is False

    async def h2(req):
        return httpx.Response(200, json=ok_response(items=[{"id": "b", "p": 0.9},
                                                           {"id": "a", "p": 0.1}]))
    c2, _ = client(tmp_path, h2)
    v = asyncio.run(c2.decide("hydra.pool_fit", {"prompt_head": "x"}, deadline_ms=50,
                              items=[{"id": "a", "text": "A"}, {"id": "b", "text": "B"}]))
    assert v.enforce is True                        # control


@pytest.mark.parametrize("v", [
    Verdict(enforce=False, action="fallback"),
    Verdict(enforce=False, action="act", label="inline"),     # shadow: would act
    Verdict(enforce=False, action="abstain"),
    Verdict(enforce=True, action="abstain"),                  # malformed: never act
    None,
    "enforce",
])
def test_gate_runs_legacy_on_everything_but_enforce(v):
    assert gate(v, lambda: "act", lambda: "legacy") == "legacy"


def test_gate_runs_act_on_enforce():
    assert gate(Verdict(enforce=True, action="act", label="inline"),
                lambda: "act", lambda: "legacy") == "act"


def test_sync_twin(tmp_path):
    async def h(req):
        return httpx.Response(200, json=ok_response())
    c, _ = client(tmp_path, h)
    assert c.decide_sync("router.spawn_intent", {"user_turn": "x"}, deadline_ms=50).enforce


def test_route_and_contracts_fail_open(tmp_path):
    async def h(req):
        return httpx.Response(503)
    c, _ = client(tmp_path, h)
    assert asyncio.run(c.route({"prompt_head": "x"})) is None
    assert asyncio.run(c.contracts()) == []
