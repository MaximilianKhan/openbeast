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
  - audit: the /send row carries a sha256 + length and NOT the message text,
    and a DENIAL records who probed
  - lifecycle: a spawned child is reaped and its exit status becomes the
    ledger's terminal state (0 done / >0 failed / signalled stopped)
  - the stream survives a transcript truncated or replaced UNDER it
  - anonymous is refused everywhere, the schema endpoint is gone, metrics are
    gated and health says only "ok" without a credential

Everything runs against fabricated ledger records and transcripts on disk —
no agent is ever spawned, no model is ever called, the GPU is never touched.

Run: OPENBEAST_SKIP_NETWORK_TESTS=1 pytest tests/test_chat_server.py
"""
import contextlib
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(REPO, "agents")
if AGENTS not in sys.path:
    sys.path.insert(0, AGENTS)


# ---------------------------------------------------------------------------
# NO STUB. There used to be a hand-rolled `sessions` module object here,
# standing in until the sibling agent's landed. It outlived its purpose and
# became a liability: a reviewer deleted agents/sessions.py outright and all
# 37 tests still passed — green against a stub with a DIFFERENT inbox layout
# (one flat inbox/ dir vs per-session dirs), no atomic write, no pid-start
# capture and therefore no pid-reuse defence. Every guarantee these tests
# claim about the ledger was being proved against a fake. Import the real
# module or fail loudly.
# ---------------------------------------------------------------------------

if not os.path.isfile(os.path.join(AGENTS, "sessions.py")):
    raise RuntimeError(
        "agents/sessions.py is missing — these tests exercise the REAL ledger "
        "and must never fall back to a stand-in")

import chat_server  # noqa: E402
import sessions  # noqa: E402  agents/sessions.py — the real ledger


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
        # Children we spawn import sessions fresh, so they need it in the
        # environment too — never the real .run/sessions of this repo.
        monkeypatch.setenv("OPENBEAST_SESSIONS_DIR", str(self.sdir))
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
    def _client(self, headers=None):
        if self._app is None:
            self._app = chat_server.create_app()
        # A REAL Host header: TrustedHostMiddleware now pins it, and
        # TestClient's default "testserver" is exactly the kind of foreign
        # name a rebinding attack arrives under.
        return TestClient(self._app, base_url="http://127.0.0.1:3003",
                          headers=dict(headers or {}))

    @property
    def client(self):
        """An IDENTIFIED caller. There is no anonymous access any more, so the
        default client carries a tailnet login the way `tailscale serve`
        injects one; a per-request `headers=` still overrides it."""
        return self._client(HDR_OK)

    @property
    def anon(self):
        """A caller with no credential of any kind — the rebinding attacker."""
        return self._client()

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
    # NEVER the real systemd: run inside a user unit (the rig's own service, a
    # CI runner) every job-spawning test would otherwise create REAL transient
    # scopes. The tests that are ABOUT the scope prefix reset this themselves.
    monkeypatch.setattr(chat_server, "_SCOPE_PREFIX", [])
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
# Process helpers
# ---------------------------------------------------------------------------

def is_zombie(pid) -> bool:
    """/proc state Z. An unreaped child is still LISTED in /proc, which is
    exactly why dropping the Popen handle made dead sessions look alive."""
    try:
        with open(f"/proc/{int(pid)}/stat", "rb") as f:
            raw = f.read().decode("utf-8", "replace")
    except (OSError, ValueError, TypeError):
        return False
    _, sep, rest = raw.rpartition(")")
    if not sep:
        return False
    fields = rest.split()
    return bool(fields) and fields[0] == "Z"


def wait_state(sid, *states, timeout=15.0):
    """Wait for the PRODUCT to move a session into one of `states`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = sessions.get(sid)
        if rec and rec.get("state") in states:
            return rec
        time.sleep(0.05)
    return None


def stream_opens(rig):
    """How many SSE attachments the server has AUDITED so far."""
    return len([a for a in rig.audit_rows()
                if a["route"] == "GET /events" and a["outcome"] == "stream_open"])


def wait_attached(rig, before, timeout=15.0):
    """Block until a NEW stream is attached.

    A fixed sleep races the reader: `rig.client` builds the app lazily inside
    the request, so a producer thread on a timer can do its work before the
    stream has opened at all — which silently turns a mid-stream test into an
    at-open test that proves nothing.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stream_opens(rig) > before:
            return True
        time.sleep(0.02)
    return False


def marker_wait(path, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.05)
    return False


@pytest.fixture()
def victim():
    """Spawn real, disposable processes in their own group — and make sure
    every one of them is gone at teardown, killed BY PID, never by pattern.

    Nothing in this file may stub out the function that does the killing: an
    escalation test whose signals go to a list proves only that a list can
    hold a signal.
    """
    procs = []
    reapers = []

    def spawn(script: str) -> subprocess.Popen:
        proc = subprocess.Popen(["/bin/bash", "-c", script],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL,
                                start_new_session=True)
        procs.append(proc)
        # Reap it the instant it dies so the ledger sees a GONE process and
        # not a zombie — this fixture's children belong to pytest, not to the
        # server, so the server's own reaper is not in play here.
        t = threading.Thread(target=proc.wait, daemon=True)
        t.start()
        reapers.append(t)
        return proc

    yield spawn

    for proc in procs:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)   # by pid, recorded above
            except OSError:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    for t in reapers:
        t.join(timeout=5)


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
    """A pid that EXITED, and a pid that was REUSED.

    The old version used an impossible pid, so it only ever proved "no such
    process" — the start-time comparison, which is the entire defence against
    pid reuse on a box that spawns thousands of eval subprocesses a day, was
    never executed by any test.
    """
    # 1. a real child that really exited
    proc = subprocess.Popen(["/bin/true"], start_new_session=True)
    pid = proc.pid
    proc.wait()                      # reap it: no zombie to confuse /proc
    sid = rig.session(kind="agent", state="running", pid=pid)
    d = rig.client.get(f"/api/chat/sessions/{sid}").json()
    assert d["session"]["state"] == "lost"

    # 2. a pid that is very much ALIVE (this test process) but is not the
    #    process the record was made for — only the start time can tell.
    sid2 = rig.session(kind="agent", state="running", pid=os.getpid())
    rec = sessions.get(sid2)
    assert rec["state"] == "running"                  # alive and ours
    real_start = rec["meta"]["pid_start"]
    assert real_start == sessions.pid_start_time(os.getpid())
    sessions.touch(sid2, meta={"pid_start": int(real_start) + 1})
    d2 = rig.client.get(f"/api/chat/sessions/{sid2}").json()
    assert d2["session"]["state"] == "lost", "pid reuse was not detected"


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
    """The producer is mid-write WHILE WE READ.

    The old version wrote the half line before opening the stream, so it only
    proved the open-time read. The hazard is a producer that flushes half an
    event between two poll passes — which is the normal case for a line the
    runner is in the middle of writing.
    """
    sid = rig.session(kind="agent", state="running",
                      lines=[{"type": "assistant", "content": "complete"}])
    path = sessions.get(sid)["transcript"]
    first = os.path.getsize(path)
    half = '{"type": "assistant", "content": "the second half arrives late"}'

    attached = stream_opens(rig)

    def producer():
        assert wait_attached(rig, attached)
        time.sleep(0.1)
        with open(path, "a") as f:            # half an event, then a pause
            f.write(half[:30])
            f.flush()
        time.sleep(0.3)
        with open(path, "a") as f:            # ...and now the rest of it
            f.write(half[30:] + "\n")
        time.sleep(0.2)
        sessions.finalize(sid, "done", summary="fin")

    t = threading.Thread(target=producer, daemon=True)
    t.start()
    try:
        frames = drain(rig.client, sid, frm=0)
    finally:
        t.join(timeout=5)

    body = [f for f in frames if f["event"] not in ("hello", "end")]
    # two WHOLE events, never a third half one, and every id is a real line
    # boundary in the finished file
    assert [f["data"]["content"] for f in body] == [
        "complete", "the second half arrives late"]
    offs = line_offsets(path)
    assert [f["id"] for f in body] == offs
    assert offs[0] == first
    assert frames[-1]["id"] == os.path.getsize(path)


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

    attached = stream_opens(rig)

    def later():
        assert wait_attached(rig, attached)
        time.sleep(0.1)
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


def test_escalation_sigterm_alone_finalizes_stopped(rig, victim):
    """The POLITE branch. Nothing is stubbed: a real process in its own group
    really receives SIGTERM and really dies.

    The old test replaced signal_session with a recorder, so the process never
    died, the loop always ran on to SIGKILL, and the SIGTERM branch — which
    did not finalize at all — was never executed by any test. An operator
    stop that worked first time therefore landed on `lost`, which reads as
    "it crashed".
    """
    proc = victim("sleep 60")
    sid = rig.session(kind="agent", state="running", pid=proc.pid)

    t = chat_server.start_escalation(sid, 0.05, 30.0, poll=0.02)
    t.join(timeout=10)
    assert not t.is_alive()
    assert proc.returncode == -signal.SIGTERM      # it went on the first ask
    rec = sessions.get(sid)
    assert rec["state"] == "stopped", rec
    assert "SIGTERM" in (rec["summary"] or "")


def test_escalation_escalates_to_sigkill_when_term_is_ignored(rig, victim):
    """The FORCEFUL branch, against a process that really ignores SIGTERM."""
    proc = victim('trap "" TERM; while true; do sleep 0.1; done')
    sid = rig.session(kind="agent", state="running", pid=proc.pid)

    t = chat_server.start_escalation(sid, 0.05, 0.4, poll=0.02)
    t.join(timeout=10)
    assert not t.is_alive()
    assert proc.returncode == -signal.SIGKILL
    rec = sessions.get(sid)
    assert rec["state"] == "stopped", rec
    assert "SIGKILL" in (rec["summary"] or "")


def test_signal_session_refuses_a_recycled_or_unproven_pid(rig, victim):
    """killpg on a stale record is how you take down an unrelated process
    group. Identity is re-verified immediately before the signal."""
    proc = victim("sleep 60")
    sid = rig.session(kind="agent", state="running", pid=proc.pid)
    rec = sessions.get(sid)

    # the pid is alive but the record is about a DIFFERENT process
    stale = dict(rec, meta=dict(rec["meta"],
                                pid_start=int(rec["meta"]["pid_start"]) + 1))
    assert chat_server.signal_session(stale, signal.SIGTERM) is False
    # a record with no proof of identity at all is not signallable either
    unproven = dict(rec, meta={})
    assert chat_server.signal_session(unproven, signal.SIGTERM) is False
    assert proc.poll() is None, "an unrelated process group was signalled"

    # ...and the honest record still works
    assert chat_server.signal_session(rec, signal.SIGTERM) is True
    proc.wait(timeout=5)
    assert proc.returncode == -signal.SIGTERM


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
    """The whole point of the feature, end to end — and NOBODY hand-writes the
    terminal state.

    The old version finished with `sessions.finalize(sid, "done")`, writing by
    hand the exact record the product never wrote. That one line hid the
    defect this test exists to catch: the child was left unreaped, stayed a
    zombie, and the session read `running` forever.
    """
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
    assert rec["meta"]["started_by"] and rec["meta"]["command"]
    # the transcript is archived in the agent log dir, NOT under SESSIONS_DIR
    # (sessions.prune() deletes a session's own directory wholesale)
    assert rec["transcript"].startswith(str(rig.logs))
    assert not rec["transcript"].startswith(str(rig.sdir))
    assert marker_wait(marker)
    assert "hello" in open(rec["transcript"]).read()

    # the product, not the test, moves it off `running`
    assert wait_state(sid, "done"), sessions.get(sid)
    assert sessions.get(sid)["state"] == "done"
    assert sid not in chat_server._CHILDREN          # reaped, not leaked
    assert not is_zombie(rec["pid"])


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
    # "/" is the console DOCUMENT and is deliberately not gated — a browser
    # cannot put a header on a document request, the page carries no session
    # data, and rebinding is stopped by the Host check, not by this. Every
    # route that returns DATA is what must refuse. Asserted separately below.
    for method, path, kw in [
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
    assert rig.anon.get("/api/chat/sessions").status_code == 404
    # ...and nothing they did reached the inbox
    assert not os.path.exists(sessions.inbox_path(sid))


    # ...and the console document IS served: it is markup, not data.
    doc = c.get("/")
    assert doc.status_code == 200, "the console document must load"

def test_listed_login_reads_but_cannot_write_without_a_device_key(rig):
    sid = rig.session(kind="agent", state="running")
    rig.operators(LISTED)
    c = rig.client
    assert c.get("/api/chat/sessions", headers=HDR_OK).status_code == 200
    assert c.get(f"/api/chat/sessions/{sid}", headers=HDR_OK).status_code == 200
    # the stream is a read too (drained against a finished session so it closes)
    finished = rig.session(kind="agent", state="done")
    assert [f["event"] for f in drain(c, finished, frm=0, headers=HDR_OK)][0] == "hello"
    # Writes need a credential, and the refusal is INDISTINGUISHABLE from the
    # one an unlisted login gets: the old 401-here/404-there split told a
    # prober that the login they just guessed IS in CHAT_OPERATORS.
    assert c.post(f"/api/chat/sessions/{sid}/send", json={"text": "x"},
                  headers=HDR_OK).status_code == 404
    assert c.post(f"/api/chat/sessions/{sid}/stop", json={},
                  headers=HDR_OK).status_code == 404
    assert c.post("/api/chat/sessions", json={"kind": "job", "cmd": "true"},
                  headers=HDR_OK).status_code == 404
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
    assert rig.anon.get("/api/chat/sessions",
                        headers={"X-OpenBeast-Local": "deadbeef"}
                        ).status_code == 404
    # A hostile non-ASCII token must not 500. Starlette decodes header bytes
    # as latin-1 and hmac.compare_digest raises TypeError on any non-ASCII
    # str — comparing BYTES is what keeps this a 404 instead of a traceback
    # sprayed into the stack log by an unauthenticated caller.
    assert rig.anon.get("/api/chat/sessions",
                        headers={"X-OpenBeast-Local": b"\xff\xfe"}
                        ).status_code == 404


def test_open_mode_still_demands_an_identity(rig):
    """No OPENBEAST_CHAT_OPERATORS = single-user default: ANY IDENTIFIED login
    reads. That is not the same as anonymous, and it used to be — a request
    with no headers at all streamed any transcript on the rig."""
    sid = rig.session(kind="agent", state="done")
    assert rig.client.get("/api/chat/sessions").status_code == 200
    assert rig.anon.get("/api/chat/sessions").status_code == 404
    assert rig.anon.get(f"/api/chat/sessions/{sid}/events?from=0"
                        ).status_code == 404


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
    rows = [a for a in rig.audit_rows() if a["route"] == "POST /send"]
    assert [r["outcome"] for r in rows] == ["http_404", "http_404"]
    # E9: the row names who probed. A denial logged as `login: null` records
    # that somebody tried and nothing about who — the probe is the whole
    # reason this log exists.
    assert rows[0]["login"] == OTHER
    assert rows[1]["login"] == LISTED


def test_stream_open_is_audited_with_its_offset(rig):
    sid = rig.session(kind="agent", state="done")
    drain(rig.client, sid, frm=7)
    rows = [a for a in rig.audit_rows() if a["route"] == "GET /events"]
    assert rows[-1]["outcome"] == "stream_open" and rows[-1]["from"] == 7


# ---------------------------------------------------------------------------
# E3 — the ledger tells the truth about a dead session
#
# The premise of the whole feature. A session started from the phone has NO
# job wrapper and NO other writer: if this server does not reap its child and
# record the exit status, the child becomes a zombie, /proc still lists it,
# the liveness check says `running` forever, the SSE stream never ends and
# /send happily queues into a corpse.
# ---------------------------------------------------------------------------

def _spawn_job(rig, cmd, tmp_path, title="job"):
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "job", "title": title, "cmd": cmd, "workdir": str(tmp_path)})
    assert r.status_code == 201, r.text
    return r.json()["session"]["id"]


def test_exit_zero_becomes_done(rig, tmp_path):
    sid = _spawn_job(rig, "exit 0", tmp_path)
    rec = wait_state(sid, "done")
    assert rec and rec["state"] == "done", sessions.get(sid)
    assert "0" in (rec["summary"] or "")
    assert not is_zombie(rec["pid"])


def test_nonzero_exit_becomes_failed(rig, tmp_path):
    sid = _spawn_job(rig, "echo nope >&2; exit 7", tmp_path)
    rec = wait_state(sid, "failed")
    assert rec and rec["state"] == "failed", sessions.get(sid)
    assert "7" in (rec["summary"] or "")
    assert not is_zombie(rec["pid"])


def test_a_signalled_job_becomes_stopped(rig, tmp_path):
    """Negative returncode = died on a signal = `stopped`, not `failed`."""
    sid = _spawn_job(rig, "kill -9 $$", tmp_path)
    rec = wait_state(sid, "stopped")
    assert rec and rec["state"] == "stopped", sessions.get(sid)
    assert "SIGKILL" in (rec["summary"] or "")
    assert not is_zombie(rec["pid"])


def test_operator_stop_of_a_spawned_job_lands_on_stopped_and_closes_the_stream(
        rig, tmp_path):
    """The headline path, end to end: start it from the API, stop it from the
    API, and the session reaches a terminal state and CLOSES ITS STREAM."""
    sid = _spawn_job(rig, "sleep 60", tmp_path)
    assert sessions.get(sid)["state"] == "running"

    r = rig.client.post(f"/api/chat/sessions/{sid}/stop", json={},
                        headers=rig.local)
    assert r.status_code == 200, r.text
    assert r.json()["signalled"] is True

    rec = wait_state(sid, "stopped")
    assert rec and rec["state"] == "stopped", sessions.get(sid)
    assert not is_zombie(rec["pid"])
    # a terminal session's stream ENDS — this is the frame a phone waits for
    frames = drain(rig.client, sid, frm=0)
    assert frames[-1]["event"] == "end"
    assert frames[-1]["data"]["state"] == "stopped"

    # and /send refuses to queue into the corpse
    assert rig.client.post(f"/api/chat/sessions/{sid}/send",
                           json={"text": "hi"},
                           headers=rig.local).status_code == 409


def test_a_running_spawned_job_is_not_terminal_yet(rig, tmp_path):
    """The reaper must not finalize anything early: a live child stays
    `running` and its stream stays open."""
    sid = _spawn_job(rig, "sleep 5", tmp_path)
    time.sleep(0.4)
    assert sessions.get(sid)["state"] == "running"
    assert sid in chat_server._CHILDREN
    proc = chat_server._CHILDREN[sid]
    try:
        assert proc.poll() is None
    finally:
        os.killpg(proc.pid, signal.SIGKILL)      # by pid, ours, recorded
    assert wait_state(sid, "stopped")


def test_reap_session_finalizes_from_the_exit_status_directly(rig):
    """Unit-level, all four exit paths through the product's own function."""
    assert chat_server.terminal_state_for(0) == "done"
    assert chat_server.terminal_state_for(3) == "failed"
    assert chat_server.terminal_state_for(-signal.SIGTERM) == "stopped"
    assert chat_server.terminal_state_for(-signal.SIGKILL) == "stopped"

    for script, expect in (("exit 0", "done"), ("exit 5", "failed"),
                           ("kill -TERM $$", "stopped"),
                           ("kill -9 $$", "stopped")):
        proc = subprocess.Popen(["/bin/bash", "-c", script],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True)
        sid = rig.session(kind="job", state="running", pid=proc.pid,
                          lines=["x"])
        assert chat_server.reap_session(sid, proc) == expect, script
        assert sessions.get(sid)["state"] == expect, script
        assert not is_zombie(proc.pid), script


def test_the_reaper_does_not_overwrite_a_terminal_state_its_owner_wrote(rig):
    """An agent stopped cooperatively writes `stopped` and then exits 0. The
    exit status must not relabel that `done`."""
    proc = subprocess.Popen(["/bin/true"], start_new_session=True)
    sid = rig.session(kind="agent", state="running", pid=proc.pid)
    proc.wait()
    sessions.finalize(sid, "stopped", summary="operator stop, went quietly")
    assert chat_server.reap_session(sid, proc) == "stopped"
    assert sessions.get(sid)["state"] == "stopped"


# ---------------------------------------------------------------------------
# E4 — one writer per record
# ---------------------------------------------------------------------------

FAKE_RUNNER = """import json, os, sys
sys.path.insert(0, {agents!r})
import sessions
argv = sys.argv[1:]
with open(os.environ["FAKE_RUNNER_ARGV"], "w") as f:
    f.write(json.dumps(argv))
sid = argv[argv.index("--session-id") + 1]
log = argv[argv.index("--log-file") + 1]
# The runner owns this record — title, model, and the cursor of operator
# messages it has already consumed.
sessions.register(sid, kind="agent", title="the runner's own title",
                  pid=os.getpid(), workdir=os.getcwd(), model="qwen",
                  transcript=log, meta={{"cursor": 4242}})
with open(log, "a") as f:
    f.write(json.dumps({{"type": "start", "task": "fake"}}) + "\\n")
sys.exit(int(os.environ.get("FAKE_RUNNER_EXIT", "0")))
"""


def test_agent_spawn_leaves_the_record_to_the_runner_and_annotates_it(
        rig, tmp_path, monkeypatch):
    """E4: register()ing an id the runner registers is a full overwrite racing
    a full overwrite. started_by/device/command vanish non-deterministically —
    and the runner's consumed-message CURSOR is reset, which replays the
    operator's last instruction into a resumed agent.

    E1/L3: steering is explicit argv now. The env opt-in is gone, so a spawn
    that relied on it would produce an agent that never reads its inbox.
    """
    fake = tmp_path / "fake_runner.py"
    fake.write_text(FAKE_RUNNER.format(agents=AGENTS))
    argv_dump = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_RUNNER_ARGV", str(argv_dump))
    monkeypatch.setattr(chat_server, "RUNNER_PATH", str(fake))

    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "agent", "task": "port the zig module", "max_iter": 12,
        "workdir": str(tmp_path)})
    assert r.status_code == 201, r.text
    sid = r.json()["session"]["id"]

    rec = wait_state(sid, "done")
    assert rec, sessions.get(sid)

    argv = json.loads(argv_dump.read_text())
    assert "--steer" in argv                       # explicit, never the env
    assert argv[argv.index("--session-id") + 1] == sid
    assert argv[argv.index("--max-iter") + 1] == "12"

    # the runner's fields survived — nothing here overwrote its record
    assert rec["title"] == "the runner's own title"
    assert rec["meta"]["cursor"] == 4242, "the runner's cursor was wiped"
    # ...and our provenance was MERGED in
    assert rec["meta"]["started_by"] == "local"
    assert "--session-id" in rec["meta"]["command"]


def test_agent_spawn_that_dies_before_registering_does_not_get_a_record(
        rig, tmp_path, monkeypatch):
    """We do not own the id, so we do not conjure a record for it. The spawn
    still answers 201 with the id, and the reaper simply finds nothing to
    finalize — no half-written row claiming a session that never started."""
    fake = tmp_path / "boom.py"
    fake.write_text("import sys\nsys.exit(3)\n")
    monkeypatch.setattr(chat_server, "RUNNER_PATH", str(fake))
    monkeypatch.setattr(chat_server, "reap_session",
                        _reap_fast(chat_server.reap_session))

    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "agent", "task": "t", "workdir": str(tmp_path)})
    assert r.status_code == 201, r.text
    sid = r.json()["session"]["id"]
    assert r.json()["session"]["state"] == "running"     # provisional view
    time.sleep(1.0)
    assert sessions.get(sid) is None
    assert sid not in chat_server._CHILDREN


def _reap_fast(real):
    """Same reaper, short annotate window — the child here never registers."""
    def wrapper(session_id, proc, **kw):
        kw["annotate_timeout"] = 0.3
        return real(session_id, proc, **kw)
    return wrapper


# ---------------------------------------------------------------------------
# E6 — the stream survives truncation and replacement UNDER it
# ---------------------------------------------------------------------------

def test_a_transcript_truncated_mid_stream_resets_rather_than_skipping(rig):
    """The guard used to run once, at open. A reviewer truncated a transcript
    while a reader was attached: the reader's offset stayed past EOF, it
    silently skipped everything written afterwards and, once the file grew
    back past that stale offset, was handed HALF an event."""
    sid = rig.session(kind="agent", state="running")
    path = sessions.get(sid)["transcript"]
    original = os.path.getsize(path)
    assert original > 50

    attached = stream_opens(rig)

    def rotate():
        assert wait_attached(rig, attached)      # only mid-stream proves it
        time.sleep(0.15)
        with open(path, "w") as f:               # truncate to almost nothing
            f.write(json.dumps({"type": "assistant", "content": "after"}) + "\n")
        time.sleep(0.3)
        with open(path, "a") as f:
            f.write(json.dumps({"type": "done", "summary": "fin",
                                "iterations": 1}) + "\n")
        time.sleep(0.2)
        sessions.finalize(sid, "done", summary="fin")

    t = threading.Thread(target=rotate, daemon=True)
    t.start()
    try:
        frames = drain(rig.client, sid, frm=0)
    finally:
        t.join(timeout=5)

    events = [f["event"] for f in frames]
    assert events[0] == "hello"                   # NOT a lost frame at open
    assert "lost" in events, events
    lost = [f for f in frames if f["event"] == "lost"][0]
    assert lost["data"]["reason"] == "offset_beyond_eof"
    assert lost["data"]["requested"] > lost["data"]["size"]

    # everything written after the truncation was delivered, from offset 0,
    # at the new file's real line boundaries
    after = [f for f in frames[events.index("lost"):]
             if f["event"] not in ("lost", "end")]
    assert [f["data"].get("content") or f["data"].get("summary")
            for f in after] == ["after", "fin"]
    assert [f["id"] for f in after] == line_offsets(path)
    assert frames[-1]["event"] == "end"


def test_a_transcript_replaced_mid_stream_is_detected_by_inode(rig):
    """Rotation, not truncation: the replacement is LONGER than the offset, so
    the size check alone sees nothing wrong and the reader would follow its
    old offset straight into the middle of a different file."""
    sid = rig.session(kind="agent", state="running")
    path = sessions.get(sid)["transcript"]

    attached = stream_opens(rig)

    def rotate():
        assert wait_attached(rig, attached)      # only mid-stream proves it
        time.sleep(0.15)
        tmp = path + ".new"
        with open(tmp, "w") as f:
            for i in range(12):
                f.write(json.dumps({"type": "assistant",
                                    "content": f"replacement line {i}"}) + "\n")
        os.replace(tmp, path)                     # same path, new inode
        time.sleep(0.3)
        sessions.finalize(sid, "done", summary="rotated")

    t = threading.Thread(target=rotate, daemon=True)
    t.start()
    try:
        frames = drain(rig.client, sid, frm=0)
    finally:
        t.join(timeout=5)

    events = [f["event"] for f in frames]
    assert "lost" in events, events
    lost = [f for f in frames if f["event"] == "lost"][0]
    assert lost["data"]["reason"] == "transcript_replaced"
    body = [f for f in frames[events.index("lost"):]
            if f["event"] not in ("lost", "end")]
    assert [f["data"]["content"] for f in body] == [
        f"replacement line {i}" for i in range(12)]
    assert [f["id"] for f in body] == line_offsets(path)


# ---------------------------------------------------------------------------
# E7 — identity is required and nothing announces the route table
#
# A request with NO headers at all used to stream any transcript on the rig,
# and the Host header was never checked. Those are the two halves of a
# browser-rebinding read of every transcript on the box — file contents,
# command output, everything the model has been shown — available to any page
# the operator happens to visit, before the service is ever published.
# ---------------------------------------------------------------------------

def test_anonymous_is_refused_on_every_route(rig):
    sid = rig.session(kind="agent", state="running")
    rig.operators(LISTED)
    a = rig.anon
    # "/" is the console DOCUMENT and is deliberately not gated — a browser
    # cannot put a header on a document request, the page carries no session
    # data, and rebinding is stopped by the Host check, not by this. Every
    # route that returns DATA is what must refuse. Asserted separately below.
    for method, path, kw in [
        ("GET", "/api/chat/sessions", {}),
        ("GET", "/api/chat/sessions?limit=5", {}),
        ("GET", f"/api/chat/sessions/{sid}", {}),
        ("GET", f"/api/chat/sessions/{sid}/events?from=0", {}),
        ("GET", "/api/chat/metrics", {}),
        ("POST", f"/api/chat/sessions/{sid}/send", {"json": {"text": "x"}}),
        ("POST", f"/api/chat/sessions/{sid}/stop", {"json": {}}),
        ("POST", "/api/chat/sessions", {"json": {"kind": "job", "cmd": "true"}}),
    ]:
        r = a.request(method, path, **kw)
        assert r.status_code == 404, f"{method} {path} -> {r.status_code}"
        assert "transcript" not in r.text and "session" not in r.text.lower()
    assert not os.path.exists(sessions.inbox_path(sid))
    # ...and with no operator list configured either: "single-user default"
    # never meant "no credential".
    rig.mp.delenv("OPENBEAST_CHAT_OPERATORS", raising=False)
    assert rig.anon.get("/api/chat/sessions").status_code == 404


    # ...and the console document IS served: it is markup, not data.
    doc = a.get("/")
    assert doc.status_code == 200, "the console document must load"

def test_the_schema_endpoint_is_gone(rig):
    """openapi.json published the entire write contract — every route, body
    and parameter — to a caller 404'd on all of them."""
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert rig.anon.get(path).status_code == 404, path
        # not even to an authorized reader: there is nothing to serve
        assert rig.client.get(path).status_code == 404, path


def test_metrics_require_a_read_credential(rig):
    rig.operators(LISTED)
    assert rig.anon.get("/api/chat/metrics").status_code == 404
    assert rig.client.request("GET", "/api/chat/metrics",
                              headers=HDR_BAD).status_code == 404
    ok = rig.client.get("/api/chat/metrics")
    assert ok.status_code == 200
    assert "openbeast_chat_streams_open" in ok.text


def test_health_says_only_ok_without_a_credential(rig):
    """start.sh probes this with no credential and must still see liveness —
    but counts, the ledger path and the auth posture are a map of the rig."""
    rig.session(kind="agent", state="running")
    rig.operators(LISTED)
    bare = rig.anon.get("/api/chat/health")
    assert bare.status_code == 200
    assert bare.json() == {"status": "ok"}

    full = rig.client.get("/api/chat/health").json()
    assert full["status"] == "ok"
    assert full["sessions_dir"] == str(rig.sdir)
    assert full["running"] == 1
    assert full["reads"] == "operators"


def test_the_listing_limit_is_not_a_route_oracle(rig):
    """As a typed int, `?limit=abc` was validated in the routing layer: 422,
    before auth, and only ever for a URL that exists."""
    rig.operators(LISTED)
    rig.session(kind="agent", state="done")
    assert rig.anon.get("/api/chat/sessions?limit=abc").status_code == 404
    assert rig.client.request("GET", "/api/chat/sessions?limit=abc",
                              headers=HDR_BAD).status_code == 404
    # for an authorized reader it is an honest 400
    c = rig.client
    assert c.get("/api/chat/sessions?limit=abc").status_code == 400
    assert c.get("/api/chat/sessions?limit=1").json()["count"] == 1
    assert c.get("/api/chat/sessions?limit=0").status_code == 200   # clamped
    assert c.get("/api/chat/sessions?limit=99999").status_code == 200


def test_write_routes_answer_404_for_a_missing_device_key(rig):
    """The 401/404 split was a membership oracle: a 401 told a prober that the
    login they had just guessed IS in CHAT_OPERATORS."""
    sid = rig.session(kind="agent", state="running")
    rig.operators(LISTED)
    c = rig.client
    listed_no_key = c.post(f"/api/chat/sessions/{sid}/send",
                           json={"text": "x"}, headers=HDR_OK)
    unlisted = c.post(f"/api/chat/sessions/{sid}/send",
                      json={"text": "x"}, headers=HDR_BAD)
    assert listed_no_key.status_code == unlisted.status_code == 404
    assert listed_no_key.json() == unlisted.json()
    assert listed_no_key.headers.get("content-type") == \
        unlisted.headers.get("content-type")
    assert not os.path.exists(sessions.inbox_path(sid))


def test_a_foreign_host_header_never_reaches_a_route(rig):
    """The other half of the rebinding defence: a hostile DNS name pointed at
    127.0.0.1 is refused before any handler runs."""
    app = chat_server.create_app()
    evil = TestClient(app, base_url="http://attacker.example.com",
                      headers=HDR_OK)
    assert evil.get("/api/chat/health").status_code == 400
    assert evil.get("/api/chat/sessions").status_code == 400
    good = TestClient(app, base_url="http://localhost:3003", headers=HDR_OK)
    assert good.get("/api/chat/health").status_code == 200
    assert "127.0.0.1" in app.state.allowed_hosts
    assert "*.ts.net" in app.state.allowed_hosts


def test_a_chat_scoped_device_key_is_an_identity_on_its_own(rig):
    """curl and the client CLI have no proxy to inject a login header. An
    enrolled, chat-scoped key is a stronger credential than the header, and it
    reads."""
    sid = rig.session(kind="agent", state="done")
    rig.operators(LISTED)
    key = rig.enroll("pixel", "key-chat", scopes=["chat"])
    a = rig.anon
    assert a.get("/api/chat/sessions", headers=key).status_code == 200
    assert a.get(f"/api/chat/sessions/{sid}", headers=key).status_code == 200
    # a key without the scope is not an identity
    nope = rig.enroll("laptop", "key-infer", scopes=["infer"])
    assert a.get("/api/chat/sessions", headers=nope).status_code == 404


# ---------------------------------------------------------------------------
# E8 / E9 / E10 / E11 / E21
# ---------------------------------------------------------------------------

def test_a_job_log_created_by_the_api_is_0600(rig, tmp_path):
    """scripts/job.sh writes 0600; a plain open() here took the umask and left
    every API job transcript world-readable. Command output is exactly as
    sensitive as the transcript it is quoted into."""
    sid = _spawn_job(rig, "echo secret-ish", tmp_path)
    rec = wait_state(sid, "done")
    assert rec
    mode = os.stat(rec["transcript"]).st_mode & 0o777
    assert mode == 0o600, oct(mode)


def test_the_spawn_audit_carries_the_command(rig, tmp_path):
    """The command is the one action the scope system gates, so it is the one
    thing the row must carry."""
    cmd = "echo the-command-that-ran"
    sid = _spawn_job(rig, cmd, tmp_path, title="audited")
    rows = [a for a in rig.audit_rows()
            if a["route"] == "POST /api/chat/sessions" and a["outcome"] == "ok"]
    row = rows[-1]
    assert row["session"] == sid
    assert row["command"] == cmd
    assert row["command_sha256"] == hashlib.sha256(cmd.encode()).hexdigest()
    assert row["workdir"] == str(tmp_path)
    assert row["kind"] == "job" and row["pid"] > 1
    wait_state(sid, "done")


def test_a_denied_probe_records_who_probed(rig):
    sid = rig.session(kind="agent", state="running")
    rig.operators(LISTED)
    rig.client.get(f"/api/chat/sessions/{sid}", headers=HDR_BAD)
    rig.anon.get(f"/api/chat/sessions/{sid}/events?from=0")
    rows = rig.audit_rows()
    detail = [r for r in rows if r["route"] == "GET /api/chat/sessions/{id}"][-1]
    assert detail["outcome"] == "http_404" and detail["login"] == OTHER
    stream = [r for r in rows if r["route"] == "GET /events"][-1]
    assert stream["outcome"] == "http_404" and stream["login"] == "anonymous"


def test_spawn_body_is_validated_and_max_iter_clamped(rig, tmp_path,
                                                      monkeypatch):
    fake = tmp_path / "fake_runner.py"
    fake.write_text(FAKE_RUNNER.format(agents=AGENTS))
    argv_dump = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_RUNNER_ARGV", str(argv_dump))
    monkeypatch.setattr(chat_server, "RUNNER_PATH", str(fake))
    c = rig.client

    # junk types are 400, never a 500 from deep inside the spawn
    for body in ({"kind": "agent", "task": "t", "max_iter": "abc"},
                 {"kind": "agent", "task": ["not", "a", "string"]},
                 {"kind": "agent", "task": "t", "max_iter": 1.5},
                 {"kind": "agent", "task": "t", "model": 7},
                 {"kind": "job", "cmd": {"not": "a string"}},
                 {"kind": "agent", "task": "t", "meta": "not an object"}):
        r = c.post("/api/chat/sessions", headers=rig.local, json=body)
        assert r.status_code == 400, (body, r.status_code, r.text)

    # out of range is clamped, not accepted verbatim: a negative budget means
    # the agent loop never runs at all
    for given, want in ((-5, "1"), (0, "1"), (10 ** 9, "1000"), (12, "12")):
        r = c.post("/api/chat/sessions", headers=rig.local,
                   json={"kind": "agent", "task": "t", "max_iter": given,
                         "workdir": str(tmp_path)})
        assert r.status_code == 201, r.text
        sid = r.json()["session"]["id"]
        wait_state(sid, "done", "failed")
        argv = json.loads(argv_dump.read_text())
        assert argv[argv.index("--max-iter") + 1] == want, given


def test_a_reader_revoked_mid_stream_loses_the_stream(rig):
    """Removing a login used to need a full stack restart — and an already
    open attachment survived even that."""
    rig.mp.setenv("OPENBEAST_CHAT_AUTH_RECHECK_S", "0.05")
    rig.operators(LISTED)
    sid = rig.session(kind="agent", state="running")

    attached = stream_opens(rig)

    def revoke():
        assert wait_attached(rig, attached)      # revoke an OPEN stream
        time.sleep(0.15)
        os.environ["OPENBEAST_CHAT_OPERATORS"] = OTHER   # LISTED is out
        time.sleep(1.5)
        # belt and braces: if the stream did NOT close, end the session so the
        # test cannot hang forever on a failure.
        sessions.finalize(sid, "done", summary="safety net")

    t = threading.Thread(target=revoke, daemon=True)
    t.start()
    try:
        frames = drain(rig.client, sid, frm=0)
    finally:
        t.join(timeout=8)

    assert frames[-1]["event"] == "end"
    assert frames[-1]["data"]["reason"] == "unauthorized", frames[-1]["data"]
    # and the same reader is refused a NEW stream
    assert rig.client.get(f"/api/chat/sessions/{sid}/events?from=0"
                          ).status_code == 404


def test_the_operator_list_reloads_from_a_file_too(rig):
    """Hot-reloaded the way the device registry is: stat-gated, no restart."""
    sid = rig.session(kind="agent", state="done")
    path = rig.run / "chat-operators"
    path.write_text(f"# the rig's readers\n{OTHER}\n")
    c = rig.client
    assert c.get("/api/chat/sessions", headers=HDR_OK).status_code == 404
    assert c.get("/api/chat/sessions", headers=HDR_BAD).status_code == 200
    path.write_text(f"{OTHER}\n{LISTED}\n")
    os.utime(path, (time.time() + 1, time.time() + 1))
    assert c.get("/api/chat/sessions", headers=HDR_OK).status_code == 200
    assert c.get(f"/api/chat/sessions/{sid}", headers=HDR_OK).status_code == 200


def test_paused_is_a_first_class_event(rig):
    """The runner emits `paused` when an operator pauses it. Without it in the
    closed set the console binds nothing and a paused agent looks stuck."""
    assert "paused" in chat_server.AGENT_EVENT_TYPES
    sid = rig.session(kind="agent", state="done", lines=[
        {"type": "steer", "op": "pause", "from": LISTED},
        {"type": "paused", "iteration": 4},
        {"type": "done", "summary": "resumed and finished", "iterations": 5}])
    frames = drain(rig.client, sid, frm=0)
    body = [f for f in frames if f["event"] not in ("hello", "end")]
    assert [f["event"] for f in body] == ["steer", "paused", "done"]
    assert body[1]["data"]["iteration"] == 4
    console = rig.client.get("/").text
    assert '"paused"' in console        # bound by the console too


# --- v1.4.0 adversarial review ----------------------------------------------

def test_caller_meta_cannot_forge_the_liveness_proof(rig, tmp_path):
    """`meta` is a free-form caller field that landed in the ledger's meta
    namespace unfiltered — and that namespace holds `pid_start`, the process
    identity PROOF that stops a recycled pid from making a dead session look
    alive. `sessions.register` only *setdefault*'d it, so the caller's value
    won, `_alive()` then compared it against the real /proc start time, and
    the record reconciled to `lost` while the command ran on: `/stop`
    answered "already finished" and never signalled, `/send` 409'd, and the
    stream closed. For a 19-hour job, for its whole duration.
    """
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "job", "title": "forged", "cmd": "sleep 30",
        "workdir": str(tmp_path),
        # the payload: reserved keys, plus one legitimate free-form key
        "meta": {"pid_start": 1, "cursor": 999, "note": "keep me"}})
    assert r.status_code == 201, r.text
    sid = r.json()["session"]["id"]
    rec = sessions.get(sid)

    # the server's own value, not the caller's
    assert rec["meta"]["pid_start"] == sessions.pid_start_time(rec["pid"])
    assert rec["meta"]["pid_start"] != 1
    assert rec["meta"]["cursor"] == 0
    # free-form meta still works — this is a filter, not a wall
    assert rec["meta"]["note"] == "keep me"

    # and the consequence the forgery bought: the session is alive and
    # stoppable, not a `lost` record with a running process behind it.
    assert sessions.get(sid)["state"] == "running"
    body = rig.client.get(f"/api/chat/sessions/{sid}").json()
    assert body["session"]["state"] == "running"
    stop = rig.client.post(f"/api/chat/sessions/{sid}/stop", headers=rig.local)
    assert stop.status_code == 200 and stop.json()["stopped"] is True
    assert stop.json().get("detail") != "already finished"
    assert wait_state(sid, "stopped"), sessions.get(sid)


def test_the_stream_read_is_bounded_and_still_pages_exactly(tmp_path):
    """`read_lines_from` did an uncapped `f.read()` from the offset, and its
    only caller is inside the async SSE generator — so every replay-from-zero
    (a fresh page load, the Replay button, the mid-stream `lost` reset) pulled
    a whole job transcript into one bytes object, plus a tuple per line, with
    the event loop blocked: every other attached stream and
    /api/chat/health waited behind it.

    Bounded now. The properties that must survive the bound are that no line
    is lost, none is duplicated, and the returned offset is exactly the
    resume point — so this asserts the paging, not just the cap.
    """
    p = tmp_path / "big.log"
    n = 20000
    with open(p, "wb") as f:
        for i in range(n):
            f.write((f"line {i:06d}" + "x" * 84 + "\n").encode())
    size = p.stat().st_size
    assert size > chat_server.STREAM_MAX_READ * 4, "make the fixture bigger"

    # one read is bounded...
    first, off1 = chat_server.read_lines_from(str(p), 0)
    assert 0 < len(first) < n, "the read is still unbounded"
    assert off1 <= chat_server.STREAM_MAX_READ + 1

    # ...and paging it delivers every line, once, in order
    seen, off, reads = [], 0, 0
    while True:
        lines, off = chat_server.read_lines_from(str(p), off)
        if not lines:
            break
        reads += 1
        seen.extend(t for t, _ in lines)
        assert reads < 500, "not converging"
    assert len(seen) == n
    assert seen[0].startswith("line 000000") and seen[-1].startswith(f"line {n-1:06d}")
    assert off == size, "the final offset is not the resume point"

    # a partial trailing line is still never emitted
    with open(p, "ab") as f:
        f.write(b"unterminated")
    lines, off2 = chat_server.read_lines_from(str(p), off)
    assert lines == [] and off2 == off


def test_a_newline_free_producer_cannot_wedge_the_stream(tmp_path):
    """The cap's own failure mode: with a hard byte limit and no line ending
    in the window, a naive reader returns nothing, forever, at the same
    offset — a stream that stops without ending. One long line is emitted
    and the offset advances past it, the same escape tail_transcript uses."""
    p = tmp_path / "nolf.log"
    open(p, "wb").write(b"A" * (chat_server.STREAM_MAX_LINE + 4096))
    lines, off = chat_server.read_lines_from(str(p), 0)
    assert len(lines) == 1
    assert off >= chat_server.STREAM_MAX_LINE
    assert off > 0, "the reader is wedged at offset 0"


def test_stopping_a_session_never_signals_a_group_it_does_not_lead(monkeypatch):
    """A session that registered itself into SOMEONE ELSE'S process group
    must get a bare-pid signal, not a killpg.

    `agents/runner.py` calls `sessions.register()` with no pgid, so
    `sessions.py` fills in `os.getpgid(pid)` — the group the process BELONGS
    to. Start such an agent from a non-interactive script (no job control, so
    the child inherits the script's group) and a Stop from the phone killpg'd
    the script and every sibling it had: on this rig, a campaign and all its
    stages. The old guard compared the live pgid to the recorded one, which
    catches a recycled pgid but passes a non-led one trivially — the process
    really is in that group.

    Every intended producer leads its group by construction (`job.sh` sets
    `set -m` for exactly this; the console spawns with start_new_session and
    records pgid=pid), so this costs those paths nothing.
    """
    calls = {"killpg": [], "kill": []}
    monkeypatch.setattr(chat_server.os, "killpg",
                        lambda pg, sig: calls["killpg"].append((pg, sig)))
    monkeypatch.setattr(chat_server.os, "kill",
                        lambda pid, sig: calls["kill"].append((pid, sig)))
    monkeypatch.setattr(chat_server, "signal_identity_ok", lambda rec: True)
    monkeypatch.setattr(chat_server.os, "getpgid", lambda pid: 4242)

    # (a) a LEADER — the group is this session's tree, so signal the group
    assert chat_server.signal_session({"pid": 4242, "pgid": 4242},
                                      signal.SIGTERM) is True
    assert calls["killpg"] == [(4242, signal.SIGTERM)]
    assert calls["kill"] == []

    # (b) a MEMBER of someone else's group — bare pid only
    calls["killpg"].clear(); calls["kill"].clear()
    assert chat_server.signal_session({"pid": 9001, "pgid": 4242},
                                      signal.SIGTERM) is True
    assert calls["killpg"] == [], "killed a group this session does not lead"
    assert calls["kill"] == [(9001, signal.SIGTERM)]


def test_sending_to_a_job_is_refused_not_silently_dropped(rig, tmp_path):
    """A job has no turn boundary and no inbox reader.

    Only `agents/runner.py` reads an inbox — `job.sh`'s supervisor never opens
    one — so a message sent to a job session was appended to a file nothing
    would ever read, and answered `{"queued": true, "detail": "queued — lands
    at the next turn"}`. A silently dropped operator instruction carrying a
    positive acknowledgement is the worst of both, and the console enabled its
    composer for jobs too, so it was reachable from the documented phone UI
    rather than only from curl. The file's own stop-route comment, the docs'
    capability table and its troubleshooting entry all already said jobs have
    no inbox; the send path was the one place that did not know.
    """
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "job", "title": "no inbox", "cmd": "sleep 20",
        "workdir": str(tmp_path)})
    assert r.status_code == 201, r.text
    sid = r.json()["session"]["id"]

    send = rig.client.post(f"/api/chat/sessions/{sid}/send",
                           headers=rig.local, json={"text": "please stop"})
    assert send.status_code == 409, send.text
    assert "no inbox" in send.text
    # and nothing was written — the whole point
    inbox = sessions.inbox_path(sid)
    assert not os.path.exists(inbox), f"an op was queued into {inbox}"

    # stop is still the action that works on a job
    stop = rig.client.post(f"/api/chat/sessions/{sid}/stop", headers=rig.local)
    assert stop.status_code == 200 and stop.json()["stopped"] is True
    assert wait_state(sid, "stopped"), sessions.get(sid)


def test_sending_to_an_agent_session_still_works(rig, tmp_path):
    """The guard must not close the path it exists to protect."""
    sid = sessions.new_id("agent")
    sessions.register(sid, kind="agent", title="t", pid=os.getpid(),
                      workdir=str(tmp_path), transcript=str(tmp_path / "t.jsonl"))
    send = rig.client.post(f"/api/chat/sessions/{sid}/send",
                           headers=rig.local, json={"text": "hello"})
    assert send.status_code == 200, send.text
    assert send.json()["queued"] is True


# ---------------------------------------------------------------------------
# an op that did not land is not "queued" (review [48])
# ---------------------------------------------------------------------------

def test_send_does_not_claim_queued_when_the_inbox_write_fails(rig,
                                                               monkeypatch):
    """append_op swallowed the write's failure, so a /send on a full disk was
    DROPPED while this route answered {"queued": true, "detail": "queued —
    lands at the next turn"}. A silently dropped operator instruction with a
    positive acknowledgement is the same shape of bug this route was already
    fixed for once (job sessions)."""
    sid = rig.session(kind="agent", state="running")
    monkeypatch.setattr(sessions, "append_op", lambda *a, **k: False)
    r = rig.client.post(f"/api/chat/sessions/{sid}/send",
                        json={"text": "focus on the zig ports"},
                        headers=rig.local)
    assert r.status_code == 503, r.text
    assert "NOT queued" in r.text


def test_stop_still_works_when_the_inbox_write_fails(rig, monkeypatch):
    """The ASYMMETRY is deliberate. /stop does not depend on the inbox:
    escalation signals the process group on a timer whether or not the
    cooperative op was ever read, so refusing a stop because its op could not
    be written would refuse a stop we can still deliver."""
    sid = rig.session(kind="agent", state="running", pid=os.getpid())
    monkeypatch.setattr(sessions, "append_op", lambda *a, **k: False)
    calls = []
    monkeypatch.setattr(chat_server, "start_escalation",
                        lambda *a, **k: calls.append(a))
    r = rig.client.post(f"/api/chat/sessions/{sid}/stop",
                        json={}, headers=rig.local)
    assert r.status_code == 200, r.text
    assert calls, "escalation must still be armed"


# ---------------------------------------------------------------------------
# the kill path needs the boot, not just the pid (review [54])
# ---------------------------------------------------------------------------

def test_signalling_refuses_a_record_from_another_boot(rig):
    """meta['pid_start'] is ticks since BOOT, so across a reboot the equality
    compares two different clocks and can match by coincidence — the
    killpg-a-stranger case this guard exists to prevent. Checking the boot in
    sessions.is_alive alone would fix the status column and leave this path
    open, because it reads meta itself."""
    sid = rig.session(kind="agent", state="running", pid=os.getpid())
    rec = sessions.get(sid)
    assert chat_server.signal_identity_ok(rec) is True
    rec["meta"]["boot_id"] = "00000000-0000-0000-0000-000000000000"
    assert chat_server.signal_identity_ok(rec) is False
    # a record with no boot_id at all keeps the old behaviour
    rec["meta"].pop("boot_id")
    assert chat_server.signal_identity_ok(rec) is True


# ---------------------------------------------------------------------------
# console.html invariants (review [46], [51])
# ---------------------------------------------------------------------------
# STRUCTURAL, and deliberately so: there is no browser here, so these assert
# the SOURCE carries the two properties rather than observing them. Weaker
# than a behavioural test, stronger than the nothing that covered this file
# before — a reader who deletes either line will be told which invariant they
# broke and why it mattered.

def _console_js() -> str:
    return open(chat_server.CONSOLE_PATH, encoding="utf-8").read()


def test_the_lost_frame_rewinds_the_resume_bookkeeping():
    """`lost` means the SERVER rewound to 0. `lost` frames carry no id, so the
    monotonic guard cannot lower S.offset by itself — it would reject every id
    of the replayed transcript and freeze the offset (and its localStorage
    copy) at a stale forward value, so a later same-page resume asks for an
    offset past the end and is served a silent blank."""
    js = _console_js()
    i = js.index('if(name === "lost")')
    branch = js[i:js.index("return;", i)]
    assert "S.offset = 0" in branch, branch
    assert "lsDel(OFF(S.id))" in branch, branch
    # and the rewind must come BEFORE the view reset, so nothing in between
    # can re-read the stale value
    assert branch.index("S.offset = 0") < branch.index("resetStream()")


def test_a_permanently_dead_stream_is_not_painted_as_reconnecting():
    """EventSource does not retry a non-200, and this handler never looked at
    readyState — so a stream the /events gate refuses (a caller whose only
    credential is a device key, which EventSource cannot send) was reported
    as "reconnecting" forever."""
    js = _console_js()
    i = js.index("es.onerror")
    handler = js[i:js.index("};", i)]
    assert "readyState === 2" in handler, handler
    assert "stream unavailable" in handler, handler
    # CONNECTING(0) must NOT be treated as permanent: the transient-drop path
    # is the whole reason the handler is quiet by default
    assert "readyState === 0" not in handler
    assert "reconnecting" in handler


# ---------------------------------------------------------------------------
# the docs must describe the gate that exists (review [35], [50])
# ---------------------------------------------------------------------------

def _chat_doc() -> str:
    import pathlib
    return (pathlib.Path(chat_server.__file__).resolve().parents[1]
            / "docs" / "BEAST_CHAT.md").read_text(encoding="utf-8")


def test_the_doc_does_not_call_mcp_spawned_agents_unsteerable():
    """[35] start_agent always mints an agent_id and passes it as
    --session-id, and --session-id implies --steer — so every agent the local
    model spawns through the tool server IS a ledger session with an inbox,
    steerable and stoppable by any chat-scoped device key. The doc listed it
    among the entry points that are NOT sessions, which is the opposite, and
    it is the entry point an operator is least likely to have expected."""
    import mcp_server
    src = open(mcp_server.__file__, encoding="utf-8").read()
    body = src[src.index("def start_agent("):]
    body = body[:body.index("\ndef ")]
    # it mints an id and hands it over unconditionally — no flag, no branch
    assert "session_id=agent_id" in body, body[-1500:]
    doc = _chat_doc()
    bullet = doc[doc.index("- An agent started any other way"):]
    bullet = bullet[:bullet.index("\n-")] if "\n-" in bullet else bullet
    assert "start_agent" not in bullet, bullet


def test_the_doc_does_not_promise_a_404_on_the_console_page():
    """[50] `/` and `/icon.svg` are deliberately ungated and answer 200 to an
    anonymous caller, so the documented symptom "404 on every route,
    including the console page" describes something that cannot happen — and
    a reader who loads the page and sees it render concludes their identity
    works when the API is still refusing them."""
    doc = _chat_doc()
    assert "404 on every route, including the console page" not in doc
    assert "404 on every API route" in doc


def test_the_console_page_and_icon_really_are_ungated(rig):
    """The other half of [50]: the doc's new wording is only right if these
    two routes DO answer 200 with no identity at all. Asserted here so the
    doc and the gate cannot drift apart in either direction."""
    anon = rig.anon                               # no credential of any kind
    for path in ("/", "/icon.svg"):
        r = anon.get(path)
        assert r.status_code == 200, (path, r.status_code)
    # ...and an API route with the same (absent) identity is still refused
    assert anon.get("/api/chat/sessions").status_code == 404


# ---------------------------------------------------------------------------
# main(): the two ways a start could hurt a LIVE server
# ---------------------------------------------------------------------------

def _free_port():
    import socket
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


def test_a_second_start_never_touches_the_live_servers_token(tmp_path, monkeypatch):
    """create_app() mints .run/chat-local.token. Built BEFORE the bind, a start
    that was about to die on a busy port had already replaced the live
    server's token with one nobody honours (agents/artifact_server.py fixed
    this as D18; this server had the same hole). The test BUILDS the busy
    port itself and asserts the file is byte-identical afterwards."""
    import socket
    run = tmp_path / "run"
    run.mkdir()
    token = run / "chat-local.token"
    token.write_text("the-live-servers-token")
    monkeypatch.setenv("OPENBEAST_CHAT_RUN_DIR", str(run))
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        monkeypatch.setenv("OPENBEAST_CHAT_PORT", str(busy.getsockname()[1]))
        monkeypatch.setenv("OPENBEAST_CHAT_BIND", "127.0.0.1")
        built = []
        monkeypatch.setattr(chat_server, "create_app",
                            lambda: built.append(1) or None)
        with pytest.raises(SystemExit) as exc:
            chat_server.main()
    assert exc.value.code == 1
    assert built == [], "the app (and its token) was built before the bind"
    assert token.read_text() == "the-live-servers-token"


def test_shutdown_is_bounded_even_with_a_stream_attached(tmp_path, monkeypatch):
    """Measured on v1.4.0: with one SSE client attached the server ignored
    SIGTERM indefinitely, because uvicorn's graceful wait has no bound unless
    given one and a stream never finishes by itself. Pin that main() hands
    uvicorn a bound — and, as the control, that the free-port path DOES build
    the app."""
    import uvicorn
    seen = {}

    class FakeServer:
        def __init__(self, config):
            seen["config"] = config

        def run(self, sockets=None):
            seen["sockets"] = sockets
            for sk in sockets or []:
                sk.close()

    monkeypatch.setenv("OPENBEAST_CHAT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("OPENBEAST_CHAT_PORT", str(_free_port()))
    monkeypatch.setenv("OPENBEAST_CHAT_BIND", "127.0.0.1")
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(chat_server, "_prune_ledger_soon", lambda *a, **k: None)
    chat_server.main()
    bound = seen["config"].timeout_graceful_shutdown
    assert bound is not None and 0 < bound <= 10
    assert seen["sockets"], "main() must hand uvicorn the socket it bound"
    assert (tmp_path / "run" / "chat-local.token").exists()


def test_the_ledger_is_pruned_at_start(tmp_path, monkeypatch):
    """sessions.prune() existed and had no caller, so the ledger only grew."""
    import threading
    called = threading.Event()
    seen = {}

    def fake_prune(days=30, keep_logs=False):
        seen["days"] = days
        seen["keep_logs"] = keep_logs
        called.set()
        raise RuntimeError("a prune failure must never matter")

    monkeypatch.setattr(chat_server.sessions, "prune", fake_prune)
    chat_server._prune_ledger_soon()
    assert called.wait(5) and seen["days"] == 30
    # asserted HERE: inside the thread it would be swallowed with the rest
    assert seen["keep_logs"] is True, "the automatic sweep must never delete job logs"


# ---------------------------------------------------------------------------
# Spawned sessions leave the stack's systemd unit
# ---------------------------------------------------------------------------

def _stub_systemd_run(tmp_path, rc):
    """A systemd-run that RECORDS its argv and then execs what follows `--`,
    which is exactly what the real --scope mode does (same pid)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "systemd-run.calls"
    stub = bindir / "systemd-run"
    stub.write_text(
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$*\" >> {log}\n"
        f"[[ {rc} -ne 0 ]] && exit {rc}\n"
        "while [[ $# -gt 0 && \"$1\" != -- ]]; do shift; done; shift\n"
        "exec \"$@\"\n")
    stub.chmod(0o755)
    return bindir, log


@pytest.mark.parametrize("in_service,rc,expect_scoped", [
    (True, 0, True),      # inside a unit, systemd-run works -> scoped
    (True, 1, False),     # inside a unit, no user manager  -> plain spawn
    (False, 0, False),    # a terminal start.sh             -> nothing to leave
])
def test_a_spawned_job_leaves_the_stacks_unit(tmp_path, monkeypatch, rig,
                                              in_service, rc, expect_scoped):
    """start_new_session leaves the process GROUP, not the CGROUP: under
    `./start.sh -d` every console job died with ./stop.sh and shared
    llama-server's memory cap. The stub records its calls; the two negative
    controls prove the prefix appears ONLY when it is needed and works."""
    bindir, log = _stub_systemd_run(tmp_path, rc)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(chat_server, "_in_service_cgroup", lambda: in_service)
    monkeypatch.setattr(chat_server, "_SCOPE_PREFIX", None)
    out = tmp_path / "ran.txt"
    r = rig.client.post("/api/chat/sessions", headers=rig.local,
                        json={"kind": "job", "cmd": f"echo ran > {out}",
                              "workdir": str(tmp_path)})
    assert r.status_code == 201, r.text
    for _ in range(100):
        if out.exists():
            break
        time.sleep(0.05)
    assert out.exists(), "the job must RUN in every case — scoped or not"
    calls = log.read_text().splitlines() if log.exists() else []
    spawned = [c for c in calls if "echo ran" in c]
    assert bool(spawned) is expect_scoped, calls
    if expect_scoped:
        assert spawned[0].startswith("--user --scope")
    # the ledger and the audit trail record the command AS ASKED
    rec = r.json()["session"]
    assert "systemd-run" not in json.dumps(rec)


def test_a_scoped_job_carries_its_own_memory_cap(tmp_path, monkeypatch):
    """Leaving the stack's unit leaves its MemoryMax; an UNBOUNDED phone job is
    the OOM incident the stack cap exists to prevent. The probe carries the
    same properties as the real spawn, so an old systemd falls back cleanly."""
    bindir, log = _stub_systemd_run(tmp_path, 0)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(chat_server, "_in_service_cgroup", lambda: True)
    monkeypatch.setenv("OPENBEAST_CHAT_JOB_MEM_PCT", "25")
    prefix = chat_server._probe_scope()
    assert "MemorySwapMax=0" in prefix
    cap = [a for a in prefix if a.startswith("MemoryMax=")]
    assert cap and 0 < int(cap[0].split("=")[1]) < chat_server._job_mem_max_bytes() * 5
    assert "MemoryMax=" in log.read_text(), "the PROBE must test the same flags"
    # control: 0 disables the cap, and only the cap
    monkeypatch.setenv("OPENBEAST_CHAT_JOB_MEM_PCT", "0")
    assert not [a for a in chat_server._probe_scope() if "Memory" in a]


def test_stop_of_a_job_nobody_reaps_is_stopped_not_lost(rig, tmp_path, monkeypatch):
    """The /stop handler SIGTERMs a job ITSELF, then starts the escalation
    thread — which was seeded "we sent nothing", so a job that died on that
    signal was finalized by nobody and reconciled to `lost`. Invisible while
    the spawning server always held a reaper; the normal case once jobs
    outlive a server restart."""
    proc = subprocess.Popen(["sleep", "300"], start_new_session=True)
    try:
        sid = sessions.new_id("job")
        assert sessions.register(sid, kind="job", title="orphan", pid=proc.pid,
                                 pgid=proc.pid, workdir=str(tmp_path))
        threading.Thread(target=proc.wait, daemon=True).start()   # no zombie
        r = rig.client.post(f"/api/chat/sessions/{sid}/stop", headers=rig.local,
                            json={})
        assert r.status_code == 200 and r.json()["signalled"] is True
        for _ in range(100):
            rec = chat_server.read_record_raw(sid) or {}
            if rec.get("state") not in ("running", None):
                break
            time.sleep(0.1)
        assert rec.get("state") == "stopped", rec
    finally:
        with contextlib.suppress(Exception):
            proc.kill()
