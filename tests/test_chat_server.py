#!/usr/bin/env python3
"""beast-chat sessions API (agents/chat_server.py) — stream, auth, audit.

Covers:
  - ledger listing + state/kind filtering
  - SSE: exact replay from a byte offset, monotonic + correct `id:` offsets,
    reattach that neither duplicates nor drops, terminal `end` frame,
    result_truncated on tool_call, job sessions as raw log lines
  - send: appends a well-formed `say` op to the inbox, refuses terminal
  - stop: agent => inbox op + escalation schedule; job => SIGNAL, no inbox
  - auth matrix: unlisted login 404 everywhere, listed login reads but 401 on
    writes, device key without the `chat` scope 404 on writes, loopback token
    bypasses both
  - audit: the /send row carries a sha256 + length and NOT the message text

Everything runs against fabricated ledger records and transcripts on disk —
no agent is ever spawned, no model is ever called, the GPU is never touched.

Run: OPENBEAST_SKIP_NETWORK_TESTS=1 pytest tests/test_chat_server.py
"""
import hashlib
import json
import os
import signal
import sys
import threading
import time
import types
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(REPO, "agents")
if AGENTS not in sys.path:
    sys.path.insert(0, AGENTS)


# ---------------------------------------------------------------------------
# agents/sessions.py is owned by a sibling agent. Until it lands, stand up a
# module object implementing the SAME frozen contract and register it under
# the name chat_server imports. chat_server itself is untouched — it does a
# plain `import sessions` and gets the real module the moment it exists.
# ---------------------------------------------------------------------------

def _build_sessions_stub() -> types.ModuleType:
    m = types.ModuleType("sessions")
    m.SESSIONS_DIR = "/tmp/openbeast-sessions"
    m.STATES = ("running", "done", "failed", "stopped", "lost")

    def _path(session_id):
        return os.path.join(m.SESSIONS_DIR, f"{session_id}.json")

    def new_id(kind):
        return (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(kind) +
                "-" + uuid.uuid4().hex[:8])

    def inbox_path(session_id):
        return os.path.join(m.SESSIONS_DIR, "inbox", f"{session_id}.jsonl")

    def register(session_id, *, kind, title, pid, pgid=None, workdir=None,
                 model=None, transcript=None, meta=None):
        os.makedirs(m.SESSIONS_DIR, exist_ok=True)
        now = datetime.now().isoformat(timespec="seconds")
        rec = {"id": session_id, "kind": kind, "title": title, "pid": pid,
               "pgid": pgid if pgid is not None else pid,
               "started_at": now, "updated_at": now, "state": "running",
               "workdir": workdir, "model": model, "transcript": transcript,
               "inbox": inbox_path(session_id), "summary": None,
               "meta": meta or {}}
        with open(_path(session_id), "w") as f:
            json.dump(rec, f)
        return rec

    def get(session_id):
        try:
            with open(_path(session_id)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def touch(session_id, **fields):
        rec = get(session_id)
        if not rec:
            return
        rec.update(fields)
        rec["updated_at"] = datetime.now().isoformat(timespec="seconds")
        with open(_path(session_id), "w") as f:
            json.dump(rec, f)

    def finalize(session_id, state, *, summary=None):
        touch(session_id, state=state, summary=summary)

    def list_sessions(*, state=None, kind=None, limit=None):
        out = []
        try:
            names = sorted(os.listdir(m.SESSIONS_DIR))
        except OSError:
            return []
        for name in names:
            if not name.endswith(".json"):
                continue
            rec = get(name[:-5])
            if not rec:
                continue
            if state and rec.get("state") != state:
                continue
            if kind and rec.get("kind") != kind:
                continue
            out.append(rec)
        out.sort(key=lambda r: r.get("started_at") or "", reverse=True)
        return out[:limit] if limit else out

    def reconcile(record):
        rec = dict(record or {})
        if rec.get("state") == "running":
            pid = rec.get("pid")
            try:
                os.kill(int(pid), 0)
            except Exception:
                rec["state"] = "lost"
        return rec

    def append_op(session_id, op):
        p = inbox_path(session_id)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a") as f:
            f.write(json.dumps(op) + "\n")

    def read_new_ops(session_id, cursor):
        p = inbox_path(session_id)
        try:
            with open(p, "rb") as f:
                f.seek(cursor)
                buf = f.read()
        except OSError:
            return [], cursor
        ops, start = [], 0
        while True:
            nl = buf.find(b"\n", start)
            if nl < 0:
                break
            try:
                ops.append(json.loads(buf[start:nl].decode()))
            except ValueError:
                pass
            start = nl + 1
        return ops, cursor + start

    def prune(days=30):
        return 0

    for fn in (new_id, register, touch, finalize, get, list_sessions,
               reconcile, inbox_path, append_op, read_new_ops, prune):
        setattr(m, fn.__name__, fn)
    return m


REAL_SESSIONS = os.path.isfile(os.path.join(AGENTS, "sessions.py"))
if not REAL_SESSIONS and "sessions" not in sys.modules:
    sys.modules["sessions"] = _build_sessions_stub()

import chat_server  # noqa: E402
import sessions  # noqa: E402  (the real module, or the stub above)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LISTED = "max@example.com"
OTHER = "stranger@example.com"
HDR_OK = {"Tailscale-User-Login": LISTED}
HDR_BAD = {"Tailscale-User-Login": OTHER}

AGENT_LINES = [
    {"type": "start", "task": "port the zig module", "model": "qwen"},
    {"type": "iteration", "number": 1},
    {"type": "assistant", "content": "Reading the file first."},
    {"type": "tool_call", "name": "read_file", "args": {"path": "a.zig"},
     "result": "x" * 2000},
    {"type": "done", "summary": "ported", "iterations": 1,
     "tokens_total": 4321, "compactions": 0},
]


class Rig:
    """A temp .run + sessions dir, plus fabricators for records and clients."""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.mp = monkeypatch
        self.sdir = tmp_path / "sessions"
        self.run = tmp_path / "run"
        self.sdir.mkdir()
        self.run.mkdir()
        self.logs = tmp_path / "logs"
        monkeypatch.setattr(sessions, "SESSIONS_DIR", str(self.sdir))
        # Spawned sessions archive their transcript in agents/logs/. Redirect
        # it so a test run never writes into the real repo.
        monkeypatch.setattr(chat_server, "LOG_DIR", str(self.logs))
        monkeypatch.setenv("OPENBEAST_CHAT_RUN_DIR", str(self.run))
        monkeypatch.delenv("OPENBEAST_CHAT_OPERATORS", raising=False)
        # Fast polls so a terminal stream drains and closes inside a test.
        monkeypatch.setenv("OPENBEAST_CHAT_POLL_MS", "20")
        monkeypatch.setenv("OPENBEAST_CHAT_HEARTBEAT_S", "300")
        monkeypatch.setenv("OPENBEAST_CHAT_STOP_TERM_S", "30")
        monkeypatch.setenv("OPENBEAST_CHAT_STOP_KILL_S", "60")
        self._app = None

    # -- config ---------------------------------------------------------
    def operators(self, *logins):
        self.mp.setenv("OPENBEAST_CHAT_OPERATORS", ",".join(logins))

    def enroll(self, dev_id, key, scopes=None):
        path = self.run / "clients.json"
        doc = {"version": 1, "devices": []}
        if path.exists():
            doc = json.loads(path.read_text())
        dev = {"id": dev_id, "label": dev_id,
               "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
               "enrolled_at": "now", "revoked_at": None}
        if scopes is not None:
            dev["scopes"] = list(scopes)
        doc["devices"].append(dev)
        path.write_text(json.dumps(doc))
        # bust the stat cache even inside one filesystem timestamp tick
        os.utime(path, (time.time() + 1, time.time() + 1))
        return {"Authorization": f"Bearer {key}"}

    # -- app ------------------------------------------------------------
    @property
    def client(self):
        if self._app is None:
            self._app = chat_server.create_app()
        return TestClient(self._app)

    @property
    def local(self):
        """Header set proving filesystem access to this box."""
        self.client  # force the app (and therefore the token) into existence
        token = (self.run / "chat-local.token").read_text().strip()
        return {"X-OpenBeast-Local": token}

    # -- fabricators -----------------------------------------------------
    def session(self, kind="agent", lines=None, state="running", pid=None,
                title=None, transcript=True):
        sid = sessions.new_id(kind)
        path = None
        if transcript:
            suffix = "jsonl" if kind == "agent" else "log"
            tdir = self.sdir / "transcripts"
            tdir.mkdir(exist_ok=True)
            path = str(tdir / f"{sid}.{suffix}")
            body = ""
            for ln in (lines if lines is not None else
                       (AGENT_LINES if kind == "agent" else ["boot", "step 1"])):
                body += (json.dumps(ln) if isinstance(ln, dict) else str(ln)) + "\n"
            with open(path, "w") as f:
                f.write(body)
        sessions.register(sid, kind=kind, title=title or f"{kind} session",
                          pid=pid if pid is not None else os.getpid(),
                          pgid=pid if pid is not None else os.getpid(),
                          workdir=str(self.tmp), model="qwen",
                          transcript=path)
        if state != "running":
            sessions.finalize(sid, state, summary="fabricated")
        return sid

    def append(self, sid, obj):
        rec = sessions.get(sid)
        with open(rec["transcript"], "a") as f:
            f.write((json.dumps(obj) if isinstance(obj, dict) else str(obj)) + "\n")

    def audit_rows(self):
        p = self.run / "chat-audit.jsonl"
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

    def audit_raw(self):
        p = self.run / "chat-audit.jsonl"
        return p.read_text() if p.exists() else ""


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def parse_sse(text: str) -> list[dict]:
    """Raw SSE bytes -> [{id, event, data}]. Comments and bare fields skipped."""
    frames = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        fid, ev, data = None, None, []
        for line in block.split("\n"):
            if line.startswith(":"):
                continue
            if line.startswith("id: "):
                fid = int(line[4:])
            elif line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data.append(line[6:])
        if ev is None:
            continue  # `retry:` preamble
        frames.append({"id": fid, "event": ev,
                       "data": json.loads("\n".join(data)) if data else None})
    return frames


def drain(client, sid, frm=None, headers=None):
    """Read a whole SSE stream to close.

    ONLY safe on a TERMINAL session: a running one follows its transcript
    forever by design, and TestClient reads a streaming response to EOF.
    """
    url = f"/api/chat/sessions/{sid}/events"
    if frm is not None:
        url += f"?from={frm}"
    r = client.get(url, headers=headers or {})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    return parse_sse(r.text)


def line_offsets(path):
    """Cumulative byte offset after each line — the values the server must emit."""
    with open(path, "rb") as f:
        buf = f.read()
    out, pos = [], 0
    for chunk in buf.split(b"\n")[:-1]:
        pos += len(chunk) + 1
        out.append(pos)
    return out


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

def test_health_and_metrics(rig):
    c = rig.client
    h = c.get("/api/chat/health").json()
    assert h["status"] == "ok"
    assert h["sessions_dir"] == str(rig.sdir)
    m = c.get("/api/chat/metrics")
    assert m.status_code == 200
    assert "openbeast_chat_streams_open" in m.text


def test_console_is_served_with_a_strict_csp(rig):
    r = rig.client.get("/")
    assert r.status_code == 200
    assert "<title>beast-chat</title>" in r.text
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    # self-contained: nothing may be pulled from off-box
    assert "src=\"http" not in r.text and "href=\"http" not in r.text


def test_listing_and_filtering(rig):
    a = rig.session(kind="agent", state="running")
    b = rig.session(kind="agent", state="done")
    j = rig.session(kind="job", state="done", lines=["one", "two"])
    c = rig.client

    allrows = c.get("/api/chat/sessions").json()
    assert allrows["count"] == 3
    ids = {r["id"] for r in allrows["sessions"]}
    assert ids == {a, b, j}
    # the list view renders a last line per row without opening the stream
    assert all("last_line" in r and "transcript_bytes" in r
               for r in allrows["sessions"])

    done = c.get("/api/chat/sessions?state=done").json()
    assert {r["id"] for r in done["sessions"]} == {b, j}
    jobs = c.get("/api/chat/sessions?kind=job").json()
    assert {r["id"] for r in jobs["sessions"]} == {j}
    assert c.get("/api/chat/sessions?state=nonsense").status_code == 400


def test_session_detail_derives_status(rig):
    sid = rig.session(kind="agent", state="done")
    d = rig.client.get(f"/api/chat/sessions/{sid}").json()
    assert d["session"]["id"] == sid
    st = d["status"]
    assert st["iterations"] == 1
    assert st["tool_calls"] == 1
    assert st["tokens"]["total"] == 4321
    assert st["terminal"] is True
    assert st["last_event"]["type"] == "done"
    assert d["artifacts"]["events"] == f"/api/chat/sessions/{sid}/events"
    assert d["artifacts"]["inbox"]
    assert rig.client.get("/api/chat/sessions/nope").status_code == 404


def test_reconcile_moves_a_dead_pid_to_lost(rig):
    # pid 1 is init: alive but not ours. Use an impossible pid instead.
    sid = rig.session(kind="agent", state="running", pid=2 ** 22 + 7)
    d = rig.client.get(f"/api/chat/sessions/{sid}").json()
    assert d["session"]["state"] == "lost"


# ---------------------------------------------------------------------------
# The stream
# ---------------------------------------------------------------------------

def test_sse_replays_everything_with_correct_monotonic_offsets(rig):
    sid = rig.session(kind="agent", state="done")
    path = sessions.get(sid)["transcript"]
    expected = line_offsets(path)

    frames = drain(rig.client, sid, frm=0)
    assert frames[0]["event"] == "hello"
    assert frames[0]["data"]["from"] == 0
    assert frames[-1]["event"] == "end"
    assert frames[-1]["data"]["state"] == "done"

    body = [f for f in frames if f["event"] not in ("hello", "end")]
    assert [f["event"] for f in body] == [l["type"] for l in AGENT_LINES]

    # every frame carries its own end offset, in BOTH the SSE id and the
    # payload, and they are exactly the file's line boundaries
    assert [f["id"] for f in body] == expected
    assert [f["data"]["offset"] for f in body] == expected
    assert expected == sorted(expected) and len(set(expected)) == len(expected)
    # the final offset is the whole file — nothing was left behind
    assert expected[-1] == os.path.getsize(path)
    assert frames[-1]["id"] == os.path.getsize(path)


def test_sse_replay_from_an_offset_returns_exactly_the_rest(rig):
    sid = rig.session(kind="agent", state="done")
    path = sessions.get(sid)["transcript"]
    offs = line_offsets(path)

    after_second = offs[1]
    frames = drain(rig.client, sid, frm=after_second)
    body = [f for f in frames if f["event"] not in ("hello", "end")]
    assert [f["id"] for f in body] == offs[2:]
    assert [f["event"] for f in body] == [l["type"] for l in AGENT_LINES[2:]]
    assert frames[0]["data"]["from"] == after_second

    # from == EOF: nothing to replay, the stream still closes cleanly
    tail = drain(rig.client, sid, frm=offs[-1])
    assert [f["event"] for f in tail] == ["hello", "end"]


def test_reattach_neither_duplicates_nor_drops(rig):
    sid = rig.session(kind="agent", state="done")
    first = drain(rig.client, sid, frm=0)
    body1 = [f for f in first if f["event"] not in ("hello", "end")]

    # "drop" after two events, exactly as a phone losing signal would
    cut = body1[1]["id"]
    seen = body1[:2]
    second = drain(rig.client, sid, frm=cut)
    body2 = [f for f in second if f["event"] not in ("hello", "end")]

    ids1 = [f["id"] for f in seen]
    ids2 = [f["id"] for f in body2]
    assert not set(ids1) & set(ids2)                 # nothing duplicated
    assert ids1 + ids2 == [f["id"] for f in body1]   # nothing dropped
    assert ([f["data"]["type"] for f in seen + body2]
            == [l["type"] for l in AGENT_LINES])


def test_last_event_id_header_beats_a_stale_from_query(rig):
    """A reconnecting EventSource re-requests its ORIGINAL url (from=0) and
    replays the last id in this header. If `from` won, every radio blip would
    replay the whole session."""
    sid = rig.session(kind="agent", state="done")
    offs = line_offsets(sessions.get(sid)["transcript"])
    frames = drain(rig.client, sid, frm=0,
                   headers={"Last-Event-ID": str(offs[2])})
    body = [f for f in frames if f["event"] not in ("hello", "end")]
    assert [f["id"] for f in body] == offs[3:]


def test_tool_call_marks_a_truncated_result(rig):
    sid = rig.session(kind="agent", state="done")
    frames = drain(rig.client, sid, frm=0)
    tc = [f for f in frames if f["event"] == "tool_call"][0]
    assert tc["data"]["result_truncated"] is True
    assert len(tc["data"]["result"]) == chat_server.TOOL_RESULT_LIMIT

    # a short result must NOT be flagged
    sid2 = rig.session(kind="agent", state="done", lines=[
        {"type": "tool_call", "name": "bash", "args": {"command": "ls"},
         "result": "a.txt"}])
    f2 = drain(rig.client, sid2, frm=0)
    assert [f for f in f2 if f["event"] == "tool_call"][0]["data"]["result_truncated"] is False


def test_job_sessions_stream_raw_log_lines(rig):
    sid = rig.session(kind="job", state="done",
                      lines=["building…", "  warning: x", "done"])
    frames = drain(rig.client, sid, frm=0)
    body = [f for f in frames if f["event"] not in ("hello", "end")]
    assert all(f["event"] == "log" for f in body)
    assert [f["data"]["line"] for f in body] == ["building…", "  warning: x", "done"]
    assert frames[0]["data"]["kind"] == "job"


def test_unparsable_line_surfaces_rather_than_vanishing(rig):
    sid = rig.session(kind="agent", state="done",
                      lines=["{not json at all", {"type": "done", "summary": "ok"}])
    frames = drain(rig.client, sid, frm=0)
    body = [f for f in frames if f["event"] not in ("hello", "end")]
    assert body[0]["event"] == "unknown"
    assert body[0]["data"]["type"] == "unparsed"
    assert "{not json" in body[0]["data"]["raw"]


def test_offset_past_eof_reports_lost_and_restarts(rig):
    sid = rig.session(kind="agent", state="done")
    frames = drain(rig.client, sid, frm=10_000_000)
    assert frames[0]["event"] == "lost"
    assert frames[0]["data"]["reason"] == "offset_beyond_eof"
    assert frames[1]["event"] == "hello" and frames[1]["data"]["from"] == 0
    body = [f for f in frames if f["event"] not in ("hello", "end", "lost")]
    assert len(body) == len(AGENT_LINES)


def test_bad_from_is_rejected(rig):
    sid = rig.session(kind="agent", state="done")
    c = rig.client
    assert c.get(f"/api/chat/sessions/{sid}/events?from=-1").status_code == 400
    assert c.get(f"/api/chat/sessions/{sid}/events?from=abc").status_code == 400


def test_partial_trailing_line_is_never_emitted(rig):
    """The producer is mid-write. A reader must not see half an event, and the
    offset must not advance past it."""
    sid = rig.session(kind="agent", state="done",
                      lines=[{"type": "assistant", "content": "complete"}])
    path = sessions.get(sid)["transcript"]
    full = os.path.getsize(path)
    with open(path, "a") as f:
        f.write('{"type": "assistant", "content": "half a li')
    frames = drain(rig.client, sid, frm=0)
    body = [f for f in frames if f["event"] not in ("hello", "end")]
    assert len(body) == 1 and body[0]["id"] == full
    assert frames[-1]["id"] == full  # end offset stops at the last full line


def test_live_follow_delivers_lines_appended_after_the_stream_opened(rig):
    """The stream FOLLOWS the file — it does not just replay what was there.

    Driven from a thread rather than by reading the socket incrementally:
    TestClient buffers a streaming response with no backpressure, so a stream
    that never ends can never be closed from the client side. Appending and
    then finalizing from a timer gives the same proof deterministically — the
    line below was NOT on disk when the request was served, and the `end`
    frame closes the stream so the test terminates. Live follow against a
    real socket is verified separately with `curl -N`.
    """
    sid = rig.session(kind="agent", state="running",
                      lines=[{"type": "start", "task": "t"}])
    at_open = os.path.getsize(sessions.get(sid)["transcript"])

    def later():
        time.sleep(0.15)
        rig.append(sid, {"type": "assistant", "content": "live!"})
        time.sleep(0.15)
        rig.append(sid, {"type": "done", "summary": "fin", "iterations": 1})
        sessions.finalize(sid, "done", summary="fin")

    t = threading.Thread(target=later, daemon=True)
    t.start()
    try:
        frames = drain(rig.client, sid, frm=0)
    finally:
        t.join(timeout=5)

    events = [f["event"] for f in frames]
    assert events[0] == "hello" and events[-1] == "end"
    assert "start" in events and "assistant" in events and "done" in events

    live = [f for f in frames if f["event"] == "assistant"][0]
    assert live["data"]["content"] == "live!"
    # it arrived AFTER the bytes that existed when the stream opened, and its
    # offset is still exactly a file line boundary
    assert live["id"] > at_open
    assert live["id"] in line_offsets(sessions.get(sid)["transcript"])
    # offsets stay monotonic across the replay/live boundary
    ids = [f["id"] for f in frames if f["event"] != "hello"]
    assert ids == sorted(ids)
    assert frames[-1]["data"]["state"] == "done"
    assert frames[-1]["id"] == os.path.getsize(sessions.get(sid)["transcript"])


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def test_send_appends_a_well_formed_op(rig):
    sid = rig.session(kind="agent", state="running")
    r = rig.client.post(f"/api/chat/sessions/{sid}/send",
                        json={"text": "focus on the zig ports"},
                        headers=rig.local)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["queued"] is True and d["delivery"] == "next_turn"
    assert "next turn" in d["detail"]

    ops = [json.loads(x) for x in
           open(sessions.inbox_path(sid)).read().splitlines() if x.strip()]
    assert len(ops) == 1
    op = ops[0]
    assert op["op"] == "say"
    assert op["text"] == "focus on the zig ports"
    assert op["id"] == d["op_id"]
    assert op["ts"] and op["by"]
    # and the sibling's reader can pick it straight back up
    back, cursor = sessions.read_new_ops(sid, 0)
    assert [o["id"] for o in back] == [d["op_id"]]
    assert cursor == os.path.getsize(sessions.inbox_path(sid))


def test_send_refuses_a_finished_session(rig):
    sid = rig.session(kind="agent", state="done")
    r = rig.client.post(f"/api/chat/sessions/{sid}/send",
                        json={"text": "hi"}, headers=rig.local)
    assert r.status_code == 409
    assert not os.path.exists(sessions.inbox_path(sid))


def test_send_rejects_empty_and_oversized(rig):
    sid = rig.session(kind="agent", state="running")
    c = rig.client
    assert c.post(f"/api/chat/sessions/{sid}/send", json={"text": "  "},
                  headers=rig.local).status_code == 400
    big = "x" * (chat_server.MAX_MESSAGE_BYTES + 1)
    assert c.post(f"/api/chat/sessions/{sid}/send", json={"text": big},
                  headers=rig.local).status_code == 413


def test_stop_on_an_agent_queues_an_op_and_schedules_escalation(rig, monkeypatch):
    sid = rig.session(kind="agent", state="running")
    sched = []
    monkeypatch.setattr(chat_server, "start_escalation",
                        lambda *a, **k: sched.append(a) or None)
    sigs = []
    monkeypatch.setattr(chat_server, "signal_session",
                        lambda rec, sig: sigs.append(sig) or True)

    r = rig.client.post(f"/api/chat/sessions/{sid}/stop", json={},
                        headers=rig.local)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["queued"] is True and d["delivery"] == "next_turn"

    ops = [json.loads(x) for x in
           open(sessions.inbox_path(sid)).read().splitlines() if x.strip()]
    assert [o["op"] for o in ops] == ["stop"]
    assert not sigs                       # nothing signalled yet — cooperative
    assert sched and sched[0][1:3] == (30.0, 60.0)   # SIGTERM 30s, SIGKILL 60s


def test_stop_on_a_job_signals_instead_of_writing_an_inbox(rig, monkeypatch):
    sid = rig.session(kind="job", state="running", lines=["go"])
    sigs = []
    monkeypatch.setattr(chat_server, "signal_session",
                        lambda rec, sig: sigs.append((rec["id"], sig)) or True)
    sched = []
    monkeypatch.setattr(chat_server, "start_escalation",
                        lambda *a, **k: sched.append(a) or None)

    r = rig.client.post(f"/api/chat/sessions/{sid}/stop", json={},
                        headers=rig.local)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["delivery"] == "immediate" and d["queued"] is False
    assert d["signalled"] is True
    # a shell command has no turn boundary to be asked politely at
    assert sigs == [(sid, signal.SIGTERM)]
    assert not os.path.exists(sessions.inbox_path(sid))
    assert sched and sched[0][1] == 0.0


def test_escalation_thread_escalates_term_then_kill(rig, monkeypatch):
    sid = rig.session(kind="agent", state="running")
    sent = []
    monkeypatch.setattr(chat_server, "signal_session",
                        lambda rec, sig: sent.append(sig) or True)
    t = chat_server.start_escalation(sid, 0.05, 0.25, poll=0.02)
    t.join(timeout=5)
    assert not t.is_alive()
    assert sent[0] == signal.SIGTERM and sent[-1] == signal.SIGKILL
    assert sessions.get(sid)["state"] == "stopped"


def test_escalation_stops_early_when_the_session_finishes(rig, monkeypatch):
    sid = rig.session(kind="agent", state="running")
    sent = []
    monkeypatch.setattr(chat_server, "signal_session",
                        lambda rec, sig: sent.append(sig) or True)
    sessions.finalize(sid, "done", summary="went quietly")
    t = chat_server.start_escalation(sid, 0.05, 0.2, poll=0.02)
    t.join(timeout=5)
    assert not t.is_alive() and sent == []


def test_stop_on_a_finished_session_is_a_no_op(rig):
    sid = rig.session(kind="agent", state="done")
    d = rig.client.post(f"/api/chat/sessions/{sid}/stop", json={},
                        headers=rig.local).json()
    assert d["stopped"] is True and d["detail"] == "already finished"
    assert not os.path.exists(sessions.inbox_path(sid))


def test_create_session_spawns_a_job_and_registers_it(rig, tmp_path):
    marker = tmp_path / "ran.txt"
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "job", "title": "marker",
        "cmd": f"echo hello; echo {marker} > {marker}",
        "workdir": str(tmp_path)})
    assert r.status_code == 201, r.text
    d = r.json()
    sid = d["session"]["id"]
    assert d["events"] == f"/api/chat/sessions/{sid}/events"
    rec = sessions.get(sid)
    assert rec["kind"] == "job" and rec["pid"] > 1 and rec["pgid"] == rec["pid"]
    # the transcript is archived in the agent log dir, NOT under SESSIONS_DIR
    # (sessions.prune() deletes a session's own directory wholesale)
    assert rec["transcript"].startswith(str(rig.logs))
    assert not rec["transcript"].startswith(str(rig.sdir))
    for _ in range(100):
        if os.path.exists(rec["transcript"]) and marker.exists():
            break
        time.sleep(0.05)
    assert "hello" in open(rec["transcript"]).read()
    sessions.finalize(sid, "done")


def test_create_session_validates(rig):
    c = rig.client
    assert c.post("/api/chat/sessions", headers=rig.local,
                  json={"kind": "nope"}).status_code == 400
    assert c.post("/api/chat/sessions", headers=rig.local,
                  json={"kind": "agent"}).status_code == 400   # no task
    assert c.post("/api/chat/sessions", headers=rig.local,
                  json={"kind": "job"}).status_code == 400     # no cmd
    assert c.post("/api/chat/sessions", headers=rig.local,
                  json={"kind": "job", "cmd": "true",
                        "workdir": "/no/such/dir"}).status_code == 400


# ---------------------------------------------------------------------------
# Auth matrix
# ---------------------------------------------------------------------------

def test_unlisted_login_gets_404_on_every_route(rig):
    sid = rig.session(kind="agent", state="running")
    rig.operators(LISTED)
    c = rig.client
    for method, path, kw in [
        ("GET", "/", {}),
        ("GET", "/api/chat/sessions", {}),
        ("GET", f"/api/chat/sessions/{sid}", {}),
        ("GET", f"/api/chat/sessions/{sid}/events?from=0", {}),
        ("POST", f"/api/chat/sessions/{sid}/send", {"json": {"text": "x"}}),
        ("POST", f"/api/chat/sessions/{sid}/stop", {"json": {}}),
        ("POST", "/api/chat/sessions", {"json": {"kind": "job", "cmd": "true"}}),
    ]:
        r = c.request(method, path, headers=HDR_BAD, **kw)
        # 404 and never 403: a stranger must not learn that beast-chat is here
        assert r.status_code == 404, f"{method} {path} -> {r.status_code}"
    # no header at all is equally unlisted
    assert c.get("/api/chat/sessions").status_code == 404
    # ...and nothing they did reached the inbox
    assert not os.path.exists(sessions.inbox_path(sid))


def test_listed_login_reads_but_cannot_write_without_a_device_key(rig):
    sid = rig.session(kind="agent", state="running")
    rig.operators(LISTED)
    c = rig.client
    assert c.get("/api/chat/sessions", headers=HDR_OK).status_code == 200
    assert c.get(f"/api/chat/sessions/{sid}", headers=HDR_OK).status_code == 200
    # the stream is a read too (drained against a finished session so it closes)
    finished = rig.session(kind="agent", state="done")
    assert [f["event"] for f in drain(c, finished, frm=0, headers=HDR_OK)][0] == "hello"
    # writes need a credential, and say so honestly — 401, not 404
    assert c.post(f"/api/chat/sessions/{sid}/send", json={"text": "x"},
                  headers=HDR_OK).status_code == 401
    assert c.post(f"/api/chat/sessions/{sid}/stop", json={},
                  headers=HDR_OK).status_code == 401
    assert c.post("/api/chat/sessions", json={"kind": "job", "cmd": "true"},
                  headers=HDR_OK).status_code == 401
    assert not os.path.exists(sessions.inbox_path(sid))


def test_device_key_without_the_chat_scope_gets_404_on_writes(rig):
    sid = rig.session(kind="agent", state="running")
    rig.operators(LISTED)
    no_scope = rig.enroll("laptop", "key-no-scope")                # no field
    wrong = rig.enroll("phone", "key-wrong-scope", scopes=["infer"])
    good = rig.enroll("pixel", "key-chat", scopes=["infer", "chat"])
    c = rig.client

    for hdr in (no_scope, wrong):
        h = dict(HDR_OK, **hdr)
        assert c.post(f"/api/chat/sessions/{sid}/send", json={"text": "x"},
                      headers=h).status_code == 404
        assert c.post(f"/api/chat/sessions/{sid}/stop", json={},
                      headers=h).status_code == 404
    assert not os.path.exists(sessions.inbox_path(sid))

    # an unknown key is 404 too — never an oracle for which keys exist
    assert c.post(f"/api/chat/sessions/{sid}/send", json={"text": "x"},
                  headers=dict(HDR_OK, Authorization="Bearer nope")
                  ).status_code == 404

    r = c.post(f"/api/chat/sessions/{sid}/send", json={"text": "ok"},
               headers=dict(HDR_OK, **good))
    assert r.status_code == 200, r.text
    assert os.path.exists(sessions.inbox_path(sid))
    rows = [a for a in rig.audit_rows() if a["route"] == "POST /send"
            and a["outcome"] == "ok"]
    assert rows[-1]["device"] == "pixel" and rows[-1]["login"] == LISTED


def test_revoked_device_loses_write_access(rig):
    sid = rig.session(kind="agent", state="running")
    rig.operators(LISTED)
    hdr = rig.enroll("pixel", "key-chat", scopes=["chat"])
    c = rig.client
    assert c.post(f"/api/chat/sessions/{sid}/send", json={"text": "a"},
                  headers=dict(HDR_OK, **hdr)).status_code == 200
    doc = json.loads((rig.run / "clients.json").read_text())
    doc["devices"][0]["revoked_at"] = "2026-09-14T00:00:00"
    (rig.run / "clients.json").write_text(json.dumps(doc))
    os.utime(rig.run / "clients.json", (time.time() + 2, time.time() + 2))
    assert c.post(f"/api/chat/sessions/{sid}/send", json={"text": "b"},
                  headers=dict(HDR_OK, **hdr)).status_code == 404


def test_loopback_token_bypasses_both_gates(rig):
    sid = rig.session(kind="agent", state="running")
    rig.operators(LISTED)          # the local caller is NOT in this list
    c = rig.client
    local = rig.local              # and presents no device key at all
    assert c.get("/api/chat/sessions", headers=local).status_code == 200
    assert c.get(f"/api/chat/sessions/{sid}", headers=local).status_code == 200
    assert c.post(f"/api/chat/sessions/{sid}/send", json={"text": "x"},
                  headers=local).status_code == 200
    # and the token file is not readable by anyone else on the box
    mode = os.stat(rig.run / "chat-local.token").st_mode & 0o777
    assert mode == 0o600
    # a wrong token is simply not local
    assert c.get("/api/chat/sessions",
                 headers={"X-OpenBeast-Local": "deadbeef"}).status_code == 404
    # A hostile non-ASCII token must not 500. Starlette decodes header bytes
    # as latin-1 and hmac.compare_digest raises TypeError on any non-ASCII
    # str — comparing BYTES is what keeps this a 404 instead of a traceback
    # sprayed into the stack log by an unauthenticated caller.
    assert c.get("/api/chat/sessions",
                 headers={"X-OpenBeast-Local": b"\xff\xfe"}).status_code == 404


def test_open_mode_allows_reads_with_no_headers(rig):
    """No OPENBEAST_CHAT_OPERATORS = single-user default: reads are open."""
    rig.session(kind="agent", state="done")
    assert rig.client.get("/api/chat/sessions").status_code == 200


def test_write_rate_limit(rig, monkeypatch):
    monkeypatch.setenv("OPENBEAST_CHAT_RATE_PER_MIN", "3")
    sid = rig.session(kind="agent", state="running")
    c = rig.client
    codes = [c.post(f"/api/chat/sessions/{sid}/send", json={"text": "x"},
                    headers=rig.local).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3:] == [429, 429]


def test_sse_is_excluded_from_inflight_accounting(rig):
    sid = rig.session(kind="agent", state="done")
    c = rig.client
    drain(c, sid, frm=0)
    text = c.get("/api/chat/metrics").text
    assert "openbeast_chat_inflight 0" in text
    assert "openbeast_chat_streams_open 0" in text
    assert 'route="GET /events",outcome="stream_open"' in text


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def test_send_audit_has_a_hash_and_length_but_never_the_text(rig):
    sid = rig.session(kind="agent", state="running")
    secret = "the api key is hunter2 and the plan is to ship on friday"
    r = rig.client.post(f"/api/chat/sessions/{sid}/send",
                        json={"text": secret}, headers=rig.local)
    assert r.status_code == 200

    raw = rig.audit_raw()
    assert secret not in raw
    assert "hunter2" not in raw
    rows = [a for a in rig.audit_rows() if a["route"] == "POST /send"]
    row = rows[-1]
    for field in ("ts", "login", "device", "route", "session", "outcome", "ms"):
        assert field in row, field
    assert row["session"] == sid and row["outcome"] == "ok"
    assert row["message_sha256"] == hashlib.sha256(secret.encode()).hexdigest()
    assert row["message_len"] == len(secret)
    assert "text" not in row and "message" not in row
    assert os.stat(rig.run / "chat-audit.jsonl").st_mode & 0o777 == 0o600


def test_denials_are_audited(rig):
    sid = rig.session(kind="agent", state="running")
    rig.operators(LISTED)
    rig.client.post(f"/api/chat/sessions/{sid}/send", json={"text": "x"},
                    headers=HDR_BAD)
    rig.client.post(f"/api/chat/sessions/{sid}/send", json={"text": "x"},
                    headers=HDR_OK)
    outcomes = [a["outcome"] for a in rig.audit_rows()
                if a["route"] == "POST /send"]
    assert "http_404" in outcomes and "http_401" in outcomes


def test_stream_open_is_audited_with_its_offset(rig):
    sid = rig.session(kind="agent", state="done")
    drain(rig.client, sid, frm=7)
    rows = [a for a in rig.audit_rows() if a["route"] == "GET /events"]
    assert rows[-1]["outcome"] == "stream_open" and rows[-1]["from"] == 7
