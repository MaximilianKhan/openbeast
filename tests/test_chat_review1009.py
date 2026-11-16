#!/usr/bin/env python3
"""beast-chat — the 2026-10-09 review, netsec S10: open SSE streams are capped
per reader and in total, and a slot always comes back.

Each case builds its own fixture (temp ledger, temp .run, in-process app);
no real stack, port or model.

Run: python3 -m pytest tests/test_chat_review1009.py -q
"""
import gc
import os
import sys
import threading
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
from test_chat_server import LISTED, OTHER, Rig, parse_sse, stream_opens  # noqa: E402

A = {"Tailscale-User-Login": LISTED}
B = {"Tailscale-User-Login": OTHER}


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_server, "_SCOPE_PREFIX", [])
    return Rig(tmp_path, monkeypatch)


class Held:
    """Streams parked on a RUNNING session, each in its own thread (TestClient
    reads a streaming body to EOF). close() ends the session, which ends them."""

    def __init__(self, rig, sid):
        self.rig, self.sid = rig, sid
        self.threads, self.results = [], []
        # Build the ONE app now: Rig creates it lazily, and two threads racing
        # that would each get their own app (and their own counters).
        rig._client()
        # Safety net: a stream that SHOULD have been refused follows the
        # running session forever. End it, so a missing cap fails an
        # assertion instead of hanging the suite.
        self.net = threading.Timer(8, sessions.finalize, (sid, "done"), {"summary": "safety net"})
        self.net.daemon = True
        self.net.start()

    def open(self, headers, n=1):
        want = stream_opens(self.rig) + n
        for _ in range(n):
            t = threading.Thread(target=self._one, args=(headers,), daemon=True)
            t.start()
            self.threads.append(t)
        deadline = time.monotonic() + 10
        while stream_opens(self.rig) < want:
            assert time.monotonic() < deadline, "the streams never attached"
            time.sleep(0.02)

    def _one(self, headers):
        r = self.rig._client(headers).get(f"/api/chat/sessions/{self.sid}/events?from=0")
        self.results.append(r.status_code)

    def close(self):
        self.net.cancel()
        sessions.finalize(self.sid, "done", summary="test over")
        for t in self.threads:
            t.join(timeout=15)
        assert not any(t.is_alive() for t in self.threads), "a held stream never closed"


def events(rig, sid, headers):
    return rig._client(headers).get(f"/api/chat/sessions/{sid}/events?from=0")


def test_one_reader_is_capped_and_another_is_not(rig):
    rig.mp.setenv("OPENBEAST_CHAT_SSE_MAX_PER_READER", "2")
    sid = rig.session(kind="agent", state="running")
    held = Held(rig, sid)
    try:
        held.open(A, 2)
        r = events(rig, sid, A)
        assert r.status_code == 429, r.text
        assert r.headers["retry-after"] == "5"
        # the message names the cause and the knob
        assert LISTED in r.json()["detail"] and "OPENBEAST_CHAT_SSE_MAX_PER_READER" in r.json()["detail"]
        assert rig.audit_rows()[-1]["outcome"] == "http_429"
        # a refusal holds nothing, and nothing else of the reader's is refused
        assert events(rig, sid, A).status_code == 429
        assert rig._client(A).get("/api/chat/sessions").status_code == 200
        # the control: a different reader still gets a stream
        held.open(B, 1)
    finally:
        held.close()
    assert sorted(held.results) == [200, 200, 200]
    # every slot came back: the capped reader can attach again (the session is
    # terminal now, so this drains and closes), more than once
    for _ in range(3):
        r = events(rig, sid, A)
        assert r.status_code == 200 and parse_sse(r.text)[-1]["event"] == "end"


def test_the_rig_wide_cap_holds_across_readers(rig):
    rig.mp.setenv("OPENBEAST_CHAT_SSE_MAX", "2")
    sid = rig.session(kind="agent", state="running")
    held = Held(rig, sid)
    try:
        held.open(A, 1)
        held.open(B, 1)
        for who in (A, B):
            r = events(rig, sid, who)
            assert r.status_code == 429 and "OPENBEAST_CHAT_SSE_MAX" in r.json()["detail"]
            assert "PER_READER" not in r.json()["detail"]
    finally:
        held.close()
    assert events(rig, sid, A).status_code == 200


def test_the_defaults_leave_room_for_many_tabs(rig):
    # no knob set: a dozen attachments from one login is ordinary use
    sid = rig.session(kind="agent", state="running")
    held = Held(rig, sid)
    try:
        held.open(A, 12)
        held.open(B, 4)
    finally:
        held.close()
    assert held.results == [200] * 16


def test_a_refused_request_never_takes_a_slot(rig):
    rig.mp.setenv("OPENBEAST_CHAT_SSE_MAX_PER_READER", "1")
    sid = rig.session(kind="agent", state="done")
    c = rig._client(A)
    for _ in range(5):
        assert c.get("/api/chat/sessions/no-such-session/events").status_code == 404
        assert c.get(f"/api/chat/sessions/{sid}/events?from=abc").status_code == 400
    assert c.get(f"/api/chat/sessions/{sid}/events?from=0").status_code == 200


def test_a_stream_that_never_started_gives_its_slot_back(rig):
    """The client can leave between the headers and the first frame; the body
    generator then never runs, so its `finally` cannot be what frees the slot."""
    rig.mp.setenv("OPENBEAST_CHAT_SSE_MAX_PER_READER", "1")
    sid = rig.session(kind="agent", state="done")
    c = rig._client(A)
    c.get("/api/chat/sessions")                      # build the app
    route = next(r for r in rig._app.routes
                 if getattr(r, "path", "") == "/api/chat/sessions/{session_id}/events")

    class Req:
        headers = {"tailscale-user-login": LISTED}
        query_params = {}
        client = type("C", (), {"host": "127.0.0.1", "port": 50000})()
        url = type("U", (), {"path": f"/api/chat/sessions/{sid}/events"})()
        scope = {"type": "http", "client": ("127.0.0.1", 50000), "headers": []}

    resp = route.endpoint(Req(), sid)                # a response nobody will iterate
    assert c.get(f"/api/chat/sessions/{sid}/events?from=0").status_code == 429
    assert c.get("/api/chat/health").json()["streams"] == 1
    del resp
    gc.collect()
    # ...and the open-streams gauge with it (review: it stayed +1 forever)
    assert c.get("/api/chat/health").json()["streams"] == 0
    assert c.get(f"/api/chat/sessions/{sid}/events?from=0").status_code == 200
    assert c.get("/api/chat/health").json()["streams"] == 0


def _bounded(fn, *args, budget=3.0):
    """Run fn in a daemon thread; (finished, result)."""
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("v", fn(*args)),
                         daemon=True)
    t.start()
    t.join(budget)
    return (not t.is_alive()), out.get("v")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_a_fifo_or_device_transcript_never_wedges_or_floods_a_reader(tmp_path):
    """Review finding: record["transcript"] was opened with a plain open().
    A FIFO blocked every reader forever (one threadpool worker per session
    list poll); /dev/zero has getsize() == 0, so the size-bounded tail reads
    fell through to an unbounded read() and OOM'd the server."""
    fifo = str(tmp_path / "t.fifo")
    os.mkfifo(fifo)
    for path in (fifo, "/dev/zero"):
        for fn, args in ((chat_server._tail_line, (path,)),
                         (chat_server.read_lines_from, (path, 0)),
                         (chat_server.tail_start, (path, 1024)),
                         (chat_server._read_tail, (path, 4096)),
                         (chat_server.derive_status,
                          ({"transcript": path, "kind": "job"},))):
            done, _ = _bounded(fn, *args)
            assert done, f"{fn.__name__}({path}) did not return"
    assert chat_server._tail_line("/dev/zero") == ""
    assert chat_server.read_lines_from(fifo, 0) == ([], 0)
    # negative control: a regular file still reads
    reg = tmp_path / "t.log"
    reg.write_text("one\ntwo\n")
    assert chat_server._tail_line(str(reg)) == "two"
    assert chat_server.read_lines_from(str(reg), 0)[0][-1][0] == "two"


def test_the_login_socket_directory_is_ours_and_only_we_can_write_it(
        tmp_path, monkeypatch):
    """Review finding: makedirs(mode=0o700, exist_ok=True) never touches an
    existing directory, so in a 0775 .run/ any group member could unlink the
    login socket and bind their own in its place."""
    import shutil
    import tempfile
    # short: AF_UNIX paths are capped near 104 bytes and pytest's are long
    run = tempfile.mkdtemp(prefix="obsk", dir="/tmp")
    try:
        os.chmod(run, 0o775)
        sock = chat_server._unix_listener(os.path.join(run, "c.sock"))
        sock.close()
        assert os.stat(run).st_mode & 0o022 == 0
        assert os.stat(run).st_mode & 0o750 == 0o750   # read/traverse kept
        assert os.stat(os.path.join(run, "c.sock")).st_mode & 0o777 == 0o600
    finally:
        shutil.rmtree(run, ignore_errors=True)
    other = tmp_path / "theirs"
    other.mkdir()
    monkeypatch.setattr(chat_server.os, "getuid", lambda: os.stat(other).st_uid + 1)
    with pytest.raises(SystemExit):
        chat_server._unix_listener(str(other / "chat.sock"))


def test_derive_status_resumes_instead_of_rescanning(tmp_path, monkeypatch):
    """Review finding: every session open ran derive_status over the WHOLE
    transcript, json-parsing each line (0.5 s per 200 MB, a threadpool worker
    held for it). It now resumes from the last complete line it folded."""
    import json as _json
    path = tmp_path / "t.jsonl"
    lines = [{"type": "iteration", "number": i} for i in range(1, 501)]
    lines.append({"type": "tool_call", "name": "bash", "result": "x"})
    path.write_text("".join(_json.dumps(x) + "\n" for x in lines))
    rec = {"transcript": str(path), "kind": "agent", "state": "running"}
    first = chat_server.derive_status(rec)
    assert first["iterations"] == 500 and first["tool_calls"] == 1

    parsed = []
    real = chat_server.json.loads
    monkeypatch.setattr(chat_server.json, "loads",
                        lambda s, *a, **k: parsed.append(s) or real(s, *a, **k))
    with open(path, "a") as f:
        f.write(_json.dumps({"type": "done", "iterations": 501,
                             "tokens_total": 42}) + "\n")
        f.write('{"type": "iteration", "num')            # mid-write
    second = chat_server.derive_status(rec)
    assert len(parsed) <= 3, len(parsed)                  # not 502
    assert second["iterations"] == 501 and second["tokens"]["total"] == 42
    assert second["events"] == 503                        # partial counts now
    assert second["last_line"].startswith('{"type": "iteration", "num')

    # ...and gives exactly what a cold full pass would
    monkeypatch.setattr(chat_server.json, "loads", real)
    with open(path, "a") as f:
        f.write('ber": 502}\n')
    warm = chat_server.derive_status(rec)
    chat_server._STATUS_CACHE.clear()
    cold = chat_server.derive_status(rec)
    assert warm == cold and cold["iterations"] == 502 and cold["events"] == 503

    # a rewrite in place (copytruncate, then regrown) is rescanned, not resumed
    path.write_text("".join(_json.dumps({"type": "compaction", "pad": "y" * 40})
                            + "\n" for _ in range(600)))
    redo = chat_server.derive_status(rec)
    assert redo["compactions"] == 600 and redo["iterations"] == 0
