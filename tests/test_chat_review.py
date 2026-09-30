#!/usr/bin/env python3
"""beast-chat — regressions for the 2026-09-29 adversarial review, plus the
features that shipped with the fixes (new-session sheet API, pause/resume,
PWA routes, notifications, transcript export, rig status).

Every case builds its own fixture: a temp ledger, temp .run, stub HTTP
servers on ephemeral loopback ports. No real stack, no GPU, no model, and no
server is left running (every stub is shut down in a fixture finalizer).

Run: python3 -m pytest tests/test_chat_review.py -q
"""
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


# ---------------------------------------------------------------------------
# chat-security-2 / -8: the audit trail is bounded, attributable and complete
# ---------------------------------------------------------------------------

def test_audit_clips_the_claimed_login(rig):
    rig.operators("max@example.com")
    r = rig.anon.get("/api/chat/sessions/x",
                     headers={"Tailscale-User-Login": "a" * 15000})
    assert r.status_code == 404
    raw = rig.audit_raw()
    assert len(raw) < 2000, f"one denial wrote {len(raw)} bytes"
    row = rig.audit_rows()[-1]
    assert len(row["login"]) <= chat_server.AUDIT_FIELD_MAX + 1


def test_denials_are_sampled_per_peer_not_written_forever(rig, monkeypatch):
    monkeypatch.setenv("OPENBEAST_CHAT_AUDIT_DENIALS_PER_MIN", "5")
    anon = rig.anon
    for _ in range(50):
        assert anon.get("/api/chat/sessions").status_code == 404
    denials = [r for r in rig.audit_rows() if r["outcome"] == "http_404"]
    assert len(denials) == 5, len(denials)
    # Negative control: a VERIFIED caller's rows are never sampled away.
    for _ in range(20):
        assert rig.client.get("/api/chat/sessions").status_code == 200
    ok = [r for r in rig.audit_rows()
          if r["outcome"] == "ok" and r["route"] == "GET /api/chat/sessions"]
    assert len(ok) == 20


def test_audit_file_rotates_past_its_size_cap(rig, monkeypatch):
    monkeypatch.setenv("OPENBEAST_CHAT_AUDIT_MAX_MB", "0.001")   # ~1 KB
    c = rig.client
    for _ in range(40):
        c.get("/api/chat/sessions")
    live = rig.run / "chat-audit.jsonl"
    old = rig.run / "chat-audit.jsonl.1"
    assert old.exists(), "no rotation happened"
    assert live.stat().st_size < 4096
    assert (old.stat().st_mode & 0o777) == 0o600
    assert (live.stat().st_mode & 0o777) == 0o600


def test_forged_login_denial_is_marked_unverified_with_its_peer(rig):
    rig.operators("max@example.com")
    r = rig.anon.get("/api/chat/sessions",
                     headers={"Tailscale-User-Login": "eve@example.com"})
    assert r.status_code == 404
    bad = [x for x in rig.audit_rows() if x["outcome"] == "http_404"][-1]
    assert bad["verified"] is False
    assert bad["login"] == "eve@example.com"
    assert bad["peer"] == "127.0.0.1"
    # Negative control: the listed login is verified.
    assert rig.client.get("/api/chat/sessions").status_code == 200
    good = [x for x in rig.audit_rows() if x["outcome"] == "ok"][-1]
    assert good["verified"] is True and good["peer"] == "127.0.0.1"


def test_escalation_signals_are_audited(rig, monkeypatch):
    """A stop's SIGTERM/SIGKILL used to leave only a ledger summary."""
    sid = rig.session(kind="job", state="running")
    sent = []
    monkeypatch.setattr(chat_server, "signal_session",
                        lambda rec, sig: sent.append(sig) or True)
    # The job never goes away (signal_session is a stub), so the escalation
    # runs its whole course: SIGTERM now, SIGKILL after (kill - term) = 1 s.
    monkeypatch.setenv("OPENBEAST_CHAT_STOP_TERM_S", "0")
    monkeypatch.setenv("OPENBEAST_CHAT_STOP_KILL_S", "1")
    r = rig.client.post(f"/api/chat/sessions/{sid}/stop", json={},
                        headers=rig.local)
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        esc = [x for x in rig.audit_rows() if x["route"] == "stop escalation"]
        if any(x.get("signal") == "SIGKILL" for x in esc):
            break
        time.sleep(0.05)
    esc = [x for x in rig.audit_rows() if x["route"] == "stop escalation"]
    assert any(x.get("signal") == "SIGKILL" and x["delivered"] for x in esc), esc
    assert all(x["login"] == "local" and x["session"] == sid for x in esc)


def test_stream_close_is_audited_with_bytes_read(rig):
    sid = rig.session(kind="agent", state="done")
    drain(rig.client, sid)
    deadline = time.monotonic() + 5
    rows = []
    while time.monotonic() < deadline and not rows:
        rows = [x for x in rig.audit_rows() if x["outcome"] == "stream_close"]
        time.sleep(0.02)
    assert rows and rows[-1]["session"] == sid
    size = os.path.getsize(sessions.get(sid)["transcript"])
    assert rows[-1]["offset"] == size and rows[-1]["bytes"] == size
