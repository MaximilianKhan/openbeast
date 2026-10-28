#!/usr/bin/env python3
"""Review 2026-10-27, R2-7: beast-chat's `streams` gauge must not drift up
when a client leaves before the first SSE frame (the body generator never
runs, so its `finally` never decrements).

In-process app on a temp .run; no port is bound.

Run: python3 -m pytest tests/test_review1027_chat.py -q
"""
import gc
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(REPO, "agents"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import chat_server  # noqa: E402
from test_chat_server import LISTED, Rig  # noqa: E402

A = {"Tailscale-User-Login": LISTED}


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_server, "_SCOPE_PREFIX", [])
    return Rig(tmp_path, monkeypatch)


def _streams(c):
    return c.get("/api/chat/health").json()["streams"]


def _unstarted_stream(rig, sid):
    route = next(r for r in rig._app.routes
                 if getattr(r, "path", "") == "/api/chat/sessions/{session_id}/events")

    class Req:
        headers = {"tailscale-user-login": LISTED}
        query_params = {}
        client = type("C", (), {"host": "127.0.0.1", "port": 50000})()
        url = type("U", (), {"path": f"/api/chat/sessions/{sid}/events"})()
        scope = {"type": "http", "client": ("127.0.0.1", 50000), "headers": []}

    return route.endpoint(Req(), sid)


def test_an_unstarted_stream_does_not_inflate_the_gauge(rig):
    sid = rig.session(kind="agent", state="done")
    c = rig._client(A)
    assert _streams(c) == 0
    resp = _unstarted_stream(rig, sid)      # headers sent, body never pulled
    del resp
    gc.collect()
    assert _streams(c) == 0
    # The metrics line agrees.
    m = c.get("/api/chat/metrics").text
    assert "openbeast_chat_streams_open 0" in m


def test_a_completed_stream_still_counts_then_returns(rig):
    # Negative control: a stream that runs to its end still nets out to 0.
    sid = rig.session(kind="agent", state="done")
    c = rig._client(A)
    r = c.get(f"/api/chat/sessions/{sid}/events?from=0")
    assert r.status_code == 200
    assert _streams(c) == 0


def test_a_live_stream_is_counted(rig):
    # Positive control: moving the increment into the generator must not stop
    # it counting a stream that is actually being served.
    from test_chat_review1009 import Held
    sid = rig.session(kind="agent", state="running")
    held = Held(rig, sid)
    try:
        held.open(A, 2)
        assert _streams(rig._client(A)) == 2
    finally:
        held.close()
    assert _streams(rig._client(A)) == 0
