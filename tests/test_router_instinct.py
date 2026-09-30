#!/usr/bin/env python3
"""Router glue for router.spawn_intent (plan §5.9; I1, I3, I4).

The `decision_site` below is the wiring docs/BEAST_INSTINCT.md specifies,
driven through the real RouterInstinct; the `test_router_*` cases at the end
drive the REAL agents/router.py (wired in H0b) end to end: byte-identical
with ROUTER_INSTINCT=off, identity gate before instinct, skip-only enforce,
fail-open."""
from __future__ import annotations

import asyncio
import itertools
import json
import os

import httpx
import pytest

import _instinct_helpers  # noqa: F401  (puts agents/ on sys.path)
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


# ─── the REAL agents/router.py, wired (plan §5.9 / reconciliation §3) ───

class _Upstream:
    """The router's httpx client: records the classify POST and every
    proxied request. classify answers `spawn`."""

    def __init__(self, spawn=False):
        self.spawn = spawn
        self.posts, self.sent = [], []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": dict(headers or {})})
        spawn = self.spawn

        class R:
            text = '"started agent 20260930-120000-deadbeef"'

            def json(self_inner):
                if url.endswith("/start_agent"):
                    return "started agent 20260930-120000-deadbeef"
                content = ('{"spawn": true, "task": "port the whole zig suite", "workdir": "."}'
                           if spawn else '{"spawn": false, "task": "", "workdir": "."}')
                return {"choices": [{"message": {"content": content}}]}
        return R()

    def build_request(self, method, url, content=None, headers=None):
        req = {"method": method, "url": url, "content": content, "headers": dict(headers or {})}
        self.sent.append(req)
        return req

    async def send(self, req, stream=False):
        class Resp:
            status_code = 200
            headers = {"content-type": "application/json"}

            async def aiter_raw(self_inner):
                yield b'{"ok": true}'

            async def aclose(self_inner):
                pass
        return Resp()

    async def aclose(self):
        pass


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """agents/router.py with a recording upstream and a given RouterInstinct."""
    import router
    from starlette.testclient import TestClient
    monkeypatch.setattr(router, "REQUIRE_IDENTITY", False)

    def run(hook, text, *, role="admin", spawn=False):
        monkeypatch.setattr(router, "_INSTINCT", hook)
        up = _Upstream(spawn)
        body = json.dumps({"model": "m", "messages": [{"role": "user", "content": text}]}).encode()
        headers = {"Content-Type": "application/json"}
        if role:
            headers["X-OpenWebUI-User-Role"] = role
        with TestClient(router.app) as c:
            router.app.state.client = up
            r = c.post("/v1/chat/completions", content=body, headers=headers)
        asyncio.run(hook.drain())
        return r, up, body
    return run


def _legacy_classify_body(user_text):
    import router
    return {"messages": [{"role": "system", "content": router._CLASSIFIER_SYS},
                         {"role": "user", "content": user_text}],
            "temperature": 0, "max_tokens": 400,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "route", "schema": router._SCHEMA,
                                                "strict": True}}}


def test_router_off_is_byte_identical(tmp_path, wired):
    """ROUTER_INSTINCT=off (the default): no instinct call of any kind, even
    with a live client that would answer 'enforce inline'; the classify body
    is exactly the legacy one and the proxied bytes are the request's."""
    import router
    calls = []
    hook = RouterInstinct("off", make_client(tmp_path, verdict(True, "act", "inline"), calls))
    for text in ("spawn an agent to port the zig tests", "what is 2+2",
                 "handle the whole port while I'm out"):
        r, up, body = wired(hook, text)
        assert r.status_code == 200
        assert up.sent and up.sent[-1]["content"] == body
        if router._HINTS.search(text):
            assert [p["json"] for p in up.posts] == [_legacy_classify_body(text)]
        else:
            assert up.posts == []
    assert calls == []


def test_router_enforce_inline_skips_the_classify(tmp_path, wired):
    calls = []
    hook = RouterInstinct("enforce", make_client(tmp_path, verdict(True, "act", "inline"), calls))
    r, up, body = wired(hook, "what do background agents do?")
    assert r.status_code == 200 and up.posts == [] and up.sent[0]["content"] == body
    assert len(calls) == 1
    # negative control: an abstain runs today's classify
    calls2 = []
    hook2 = RouterInstinct("enforce", make_client(tmp_path, verdict(True, "abstain", "inline"), calls2))
    r, up, _ = wired(hook2, "what do background agents do?")
    assert len(up.posts) == 1 and len(calls2) == 1


def test_router_identity_gate_runs_before_instinct(tmp_path, wired):
    """Reconciliation §3: identity gate FIRST — a guest turn never reaches
    instinct (nor the classify), in any mode."""
    for mode in ("shadow", "enforce"):
        calls = []
        hook = RouterInstinct(mode, make_client(tmp_path, verdict(True, "act", "inline"), calls))
        r, up, _ = wired(hook, "spawn an agent to port it", role="user")
        assert r.status_code == 200 and calls == [] and up.posts == []


def test_router_shadow_never_changes_a_turn(tmp_path, wired):
    calls = []
    hook = RouterInstinct("shadow", make_client(tmp_path, verdict(True, "act", "inline"), calls))
    r, up, _ = wired(hook, "spawn an agent to port the zig tests", spawn=True)
    # the legacy classify still ran and still spawned
    assert [p["url"].rsplit("/", 1)[-1] for p in up.posts] == ["completions", "start_agent"]
    assert "Started a background agent" in r.text
    assert len(calls) == 1 and json.loads(calls[0].content)["ceiling"] == "shadow"


def test_router_instinct_dead_fails_open(tmp_path, wired):
    calls = []
    hook = RouterInstinct("enforce", make_client(tmp_path, httpx.ConnectError("dead"), calls))
    r, up, _ = wired(hook, "spawn an agent to port the zig tests", spawn=True)
    assert "Started a background agent" in r.text      # today's path, untouched
