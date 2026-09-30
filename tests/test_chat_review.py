#!/usr/bin/env python3
"""beast-chat — regressions for the 2026-09-29 adversarial review, plus the
features that shipped with the fixes (new-session sheet API, pause/resume,
PWA routes, notifications, transcript export, rig status).

Every case builds its own fixture: a temp ledger, temp .run, stub HTTP
servers on ephemeral loopback ports. No real stack, no GPU, no model, and no
server is left running (every stub is shut down in a fixture finalizer).

Run: python3 -m pytest tests/test_chat_review.py -q
"""
import hashlib
import json
import os
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(REPO, "agents")
TESTS = os.path.dirname(os.path.abspath(__file__))
for _p in (AGENTS, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import chat_server  # noqa: E402
import sessions  # noqa: E402
# The shared fixture (temp ledger + run dir + client factory). Imported by
# name so pytest does not collect test_chat_server's tests a second time.
from test_chat_server import Rig, drain, wait_state  # noqa: E402,F401


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_server, "_SCOPE_PREFIX", [])
    return Rig(tmp_path, monkeypatch)


def _ops(sid):
    path = sessions.inbox_path(sid)
    if not os.path.exists(path):
        return []
    return [json.loads(x) for x in open(path).read().splitlines() if x.strip()]


# ---------------------------------------------------------------------------
# chat-security-1: /send must not ack what the agent will never receive
# ---------------------------------------------------------------------------

def test_send_refuses_a_message_longer_than_the_agent_receives(rig):
    sid = rig.session(kind="agent", state="running")
    text = "A" * 10000 + " FINAL INSTRUCTION: do not delete anything"
    r = rig.client.post(f"/api/chat/sessions/{sid}/send", json={"text": text},
                        headers=rig.local)
    assert r.status_code == 413, r.text
    assert str(sessions.OP_MAX_TEXT) in r.json()["detail"]
    assert _ops(sid) == [], "a refused message still reached the inbox"


def test_send_at_exactly_the_cap_arrives_whole(rig):
    """Negative control: the largest accepted message is delivered intact."""
    sid = rig.session(kind="agent", state="running")
    text = "B" * (sessions.OP_MAX_TEXT - 5) + " END."
    r = rig.client.post(f"/api/chat/sessions/{sid}/send", json={"text": text},
                        headers=rig.local)
    assert r.status_code == 200, r.text
    ops, _ = sessions.read_new_ops(sid, 0)
    assert ops[0]["text"] == text           # not clipped, no truncation mark
