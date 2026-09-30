#!/usr/bin/env python3
"""Router glue for router.spawn_intent (plan §5.9; I1, I3, I4).

agents/router.py itself is shared wiring and is NOT edited in P0; the
`decision_site` below is the exact wiring docs/BEAST_INSTINCT.md specifies,
driven through the real RouterInstinct. The last test pins that router.py is
still byte-for-byte free of instinct until that wiring lands."""
from __future__ import annotations

import asyncio
import itertools
import os

import httpx
import pytest

import _instinct_helpers as H
from instinct.client import InstinctClient
from instinct.engines.rules import ROUTER_HINTS
from instinct.routerhook import RouterInstinct


def make_client(tmp_path, answer, calls):
    kf = tmp_path / "instinct.key"
    fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, b"k" * 40)
    os.close(fd)

    async def handler(req: httpx.Request):
        calls.append(req)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            return await answer(req)
        return httpx.Response(200, json=answer)
    return InstinctClient("http://instinct.test", kf, transport=httpx.MockTransport(handler))


def verdict(enforce, action, label):
    return {"contract": "instinct/1", "enforce": enforce, "action": action,
            "answer": {"label": label}, "items": None, "trace_id": "ins_t",
            "fallback": {"used": not enforce, "reason": None}}


async def decision_site(hook: RouterInstinct, *, admin: bool, user_text: str, classify):
    """The wiring from docs/BEAST_INSTINCT.md, verbatim in shape:
    identity gate FIRST -> instinct (skip-only) -> unchanged legacy classify."""
    if user_text and admin:                          # _spawn_allowed(request.headers)
        hinted = bool(ROUTER_HINTS.search(user_text))   # == router._HINTS (boundaries test)
        if await hook.skip_classify(user_text, hinted):
            return "passthrough(skipped-classify)"
        if hinted:
            spawn = await classify(user_text)
            if spawn:
                return "SPAWN"
    return "passthrough"


def run_site(hook, **kw):
    async def go():
        out = await decision_site(hook, **kw)
        await hook.drain()
        return out
    return asyncio.run(go())


def legacy_classify(result):
    seen = []

    async def classify(text):
        seen.append(text)
        return result
    return classify, seen


def test_off_makes_zero_instinct_calls(tmp_path):
    calls = []
    hook = RouterInstinct("off", make_client(tmp_path, verdict(True, "act", "inline"), calls))
    cls, seen = legacy_classify(False)
    for text in ("spawn an agent to do x", "what is 2+2"):
        run_site(hook, admin=True, user_text=text, classify=cls)
    assert calls == [] and seen == ["spawn an agent to do x"]   # today's behaviour


def test_default_mode_is_off(monkeypatch):
    monkeypatch.delenv("ROUTER_INSTINCT", raising=False)
    assert RouterInstinct().mode == "off"
    monkeypatch.setenv("ROUTER_INSTINCT", "ENFORCE")
    assert RouterInstinct().mode == "enforce"
    monkeypatch.setenv("ROUTER_INSTINCT", "yolo")
    assert RouterInstinct().mode == "off"               # unknown -> off, fail safe


def test_shadow_never_changes_behaviour(tmp_path):
    calls = []
    hook = RouterInstinct("shadow", make_client(tmp_path, verdict(True, "act", "inline"),
                                                calls))
    cls, seen = legacy_classify(True)
    out = run_site(hook, admin=True, user_text="spawn an agent to port it", classify=cls)
    assert out == "SPAWN" and seen                      # classify still ran
    out = run_site(hook, admin=True, user_text="handle the whole port while I'm out",
                   classify=cls)
    assert out == "passthrough"                         # non-hinted: shadowed only
    assert len(calls) == 2
    import json
    bodies = [json.loads(c.content) for c in calls]
    assert all(b["ceiling"] == "shadow" for b in bodies)
    assert [b["baseline"] for b in bodies] == ["hint", "nohint"]


def test_shadow_drops_when_full(tmp_path):
    calls = []
    gate = asyncio.Event()

    async def slow(req):
        await gate.wait()
        return httpx.Response(200, json=verdict(False, "act", "inline"))
    hook = RouterInstinct("shadow", make_client(tmp_path, slow, calls))

    async def go():
        took = [hook.shadow("x", "nohint") for _ in range(5)]
        await asyncio.sleep(0.05)
        inflight = hook.inflight
        gate.set()
        await hook.drain()
        return took, inflight
    took, inflight = asyncio.run(go())
    assert took == [True, True, False, False, False]
    assert inflight == 2 and hook.dropped == 3 and hook.inflight == 0


def test_enforce_confident_inline_skips_classify(tmp_path):
    calls = []
    hook = RouterInstinct("enforce", make_client(tmp_path, verdict(True, "act", "inline"),
                                                 calls))
    cls, seen = legacy_classify(True)
    out = run_site(hook, admin=True, user_text="what do background agents do?", classify=cls)
    assert out == "passthrough(skipped-classify)" and seen == []


@pytest.mark.parametrize("answer", [
    verdict(False, "act", "inline"),        # shadow / not promoted
    verdict(True, "abstain", "inline"),     # below threshold (0.6-ish stub)
    verdict(True, "act", "spawn"),          # I4: spawn can never be what we act on
    verdict(False, "fallback", None),
    httpx.ConnectError("dead"),             # I3: instinct unreachable
])
def test_enforce_anything_else_runs_legacy(tmp_path, answer):
    calls = []
    hook = RouterInstinct("enforce", make_client(tmp_path, answer, calls))
    cls, seen = legacy_classify(False)
    out = run_site(hook, admin=True, user_text="spawn an agent to port it", classify=cls)
    assert seen == ["spawn an agent to port it"] and out == "passthrough"


def test_never_spawns_via_instinct(tmp_path):
    """With the legacy classify saying spawn=false, NO instinct output of any
    shape can produce a spawn; with it saying true, instinct can only have
    skipped it — never added one."""
    labels = ["inline", "spawn", None, "EVIL"]
    actions = ["act", "abstain", "review", "fallback"]
    for (enforce, action, label), mode in itertools.product(
            itertools.product([True, False], actions, labels), ["off", "shadow", "enforce"]):
        hook = RouterInstinct(mode, make_client(tmp_path, verdict(enforce, action, label), []))
        cls, _ = legacy_classify(False)
        for text in ("spawn an agent to port it", "handle it all while I'm out"):
            assert run_site(hook, admin=True, user_text=text, classify=cls) != "SPAWN"


def test_non_admin_turn_never_reaches_instinct(tmp_path):
    calls = []
    hook = RouterInstinct("enforce", make_client(tmp_path, verdict(True, "act", "inline"),
                                                 calls))
    cls, seen = legacy_classify(True)
    out = run_site(hook, admin=False, user_text="spawn an agent to port it", classify=cls)
    assert out == "passthrough" and calls == [] and seen == []


def test_router_py_is_unwired_today():
    """ROUTER_INSTINCT is not wired into agents/router.py in P0 (shared
    wiring file). When it is, this test is replaced by the byte-identity
    test with ROUTER_INSTINCT=off (plan §5.13)."""
    src = (H.REPO / "agents" / "router.py").read_text()
    assert "instinct" not in src.lower()
