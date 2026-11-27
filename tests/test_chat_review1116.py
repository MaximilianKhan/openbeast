#!/usr/bin/env python3
"""beast-chat — regressions for the 2026-11-16 review (chat lens).

The headline: CHAT_LOGIN_FROM=unix decided "this request came in on the 0600
login socket" from `request.client is None`. uvicorn ALSO reports no client
for a TCP connection whose peer reset before connection_made, and still
serves the bytes it queued — so any local process could send a forged
Tailscale-User-Login over TCP loopback, reset, and be served (blind) as the
operator, with `verified: true, peer: "unix"` audit rows in their name. The
socket is now identified from the LISTENER (scope["server"] is
(socket_path, None) for AF_UNIX, (ip, port) for TCP even after a reset).

Run: python3 -m pytest tests/test_chat_review1116.py -q
"""
import os
import socket
import struct
import sys
import tempfile
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
import loginsock  # noqa: E402
import sessions  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_chat_server import Rig  # noqa: E402

LOGIN = {"Tailscale-User-Login": "victim@example.com"}


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_server, "_SCOPE_PREFIX", [])
    return Rig(tmp_path, monkeypatch)


def _scoped(app, **over):
    """The app, with the ASGI scope's `client` / `server` replaced — how a
    test stands in for a Unix-socket arrival or a reset TCP peer."""
    async def wrapped(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope = dict(scope, **over)
        await app(scope, receive, send)
    return wrapped


def _client(rig, **over):
    rig.client                                   # build the app (and token)
    return TestClient(_scoped(rig._app, **over),
                      base_url="http://127.0.0.1:3003")


SOCK = "/run/ob-test/chat.sock"


# ---------------------------------------------------------------------------
# loginsock: the socket is named by the listener, never by a missing peer
# ---------------------------------------------------------------------------

class _Req:
    def __init__(self, server, client=None):
        self.scope = {"server": server, "client": client}
        self.client = client


@pytest.mark.parametrize("server,path,ok", [
    ((SOCK, None), SOCK, True),
    (("/run/ob-test/../ob-test/chat.sock", None), SOCK, True),   # normalised
    (("/run/other.sock", None), SOCK, False),    # some other Unix socket
    (("127.0.0.1", 3003), SOCK, False),          # TCP, even with no client
    ((SOCK, 0), SOCK, False),                    # a port is never a socket
    ((SOCK, None), "", False),                   # nothing configured
    (None, SOCK, False),
])
def test_arrived_on_names_the_socket_from_the_listener(server, path, ok):
    assert loginsock.arrived_on(_Req(server), path) is ok


def test_arrived_on_refuses_what_is_not_an_asgi_request():
    class Bare:
        client = None
    assert loginsock.arrived_on(Bare(), SOCK) is False


# ---------------------------------------------------------------------------
# The trust decision, through the real app
# ---------------------------------------------------------------------------

def test_unix_mode_refuses_a_reset_tcp_peer_with_a_forged_login(rig, monkeypatch):
    """client=None on a TCP listener is a reset peer, not the socket: 404,
    and the audit row is unverified and does NOT say `unix`."""
    monkeypatch.setenv("OPENBEAST_CHAT_LOGIN_FROM", "unix")
    monkeypatch.setenv("OPENBEAST_CHAT_SOCKET", SOCK)
    c = _client(rig, client=None, server=("127.0.0.1", 3003))
    assert c.get("/api/chat/sessions", headers=LOGIN).status_code == 404
    rows = [r for r in rig.audit_rows() if r.get("login") == "victim@example.com"]
    assert rows, "the refused request was not audited"
    assert all(r["verified"] is False for r in rows), rows
    assert all(r["peer"] != "unix" for r in rows), rows
    assert rows[-1]["peer"] == "unknown"


def test_unix_mode_honours_a_login_on_its_own_socket(rig, monkeypatch):
    """Control: the same request, arriving on the configured socket."""
    monkeypatch.setenv("OPENBEAST_CHAT_LOGIN_FROM", "unix")
    monkeypatch.setenv("OPENBEAST_CHAT_SOCKET", SOCK)
    c = _client(rig, client=None, server=(SOCK, None))
    assert c.get("/api/chat/sessions", headers=LOGIN).status_code == 200
    rows = [r for r in rig.audit_rows() if r.get("login") == "victim@example.com"]
    assert rows and rows[-1]["verified"] is True and rows[-1]["peer"] == "unix"


def test_unix_mode_refuses_a_login_on_some_other_unix_socket(rig, monkeypatch):
    monkeypatch.setenv("OPENBEAST_CHAT_LOGIN_FROM", "unix")
    monkeypatch.setenv("OPENBEAST_CHAT_SOCKET", SOCK)
    c = _client(rig, client=None, server=("/tmp/elsewhere.sock", None))
    assert c.get("/api/chat/sessions", headers=LOGIN).status_code == 404


def test_loopback_mode_refuses_a_reset_peer_too(rig, monkeypatch):
    """_peer_is_loopback used to answer True for client=None — the same hole
    in the default mode, and off-box when OPENBEAST_CHAT_BIND is a LAN
    address."""
    monkeypatch.delenv("OPENBEAST_CHAT_LOGIN_FROM", raising=False)
    monkeypatch.delenv("OPENBEAST_CHAT_SOCKET", raising=False)
    c = _client(rig, client=None, server=("192.168.1.20", 3003))
    assert c.get("/api/chat/sessions", headers=LOGIN).status_code == 404
    # control: a real loopback peer still reads in loopback mode
    c = _client(rig, client=("127.0.0.1", 50000), server=("127.0.0.1", 3003))
    assert c.get("/api/chat/sessions", headers=LOGIN).status_code == 200


# ---------------------------------------------------------------------------
# End to end: a real uvicorn, real reset TCP connections
# ---------------------------------------------------------------------------

def _queue_reset_requests(port, n):
    """Connect, send a forged-login request, and close with an RST — all
    BEFORE the server accepts, so getpeername() fails on accept and uvicorn
    reports client=None while the queued bytes are still read."""
    req = (b"GET /api/chat/sessions HTTP/1.1\r\nHost: 127.0.0.1\r\n"
           b"Tailscale-User-Login: victim@example.com\r\n\r\n")
    for _ in range(n):
        s = socket.create_connection(("127.0.0.1", port))
        s.sendall(req)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        s.close()


def test_real_server_never_trusts_a_reset_tcp_peer(rig, monkeypatch):
    import uvicorn
    sock_dir = tempfile.mkdtemp(prefix="obrst", dir="/tmp")
    sock_path = os.path.join(sock_dir, "chat.sock")
    monkeypatch.setenv("OPENBEAST_CHAT_LOGIN_FROM", "unix")
    monkeypatch.setenv("OPENBEAST_CHAT_SOCKET", sock_path)
    monkeypatch.setenv("OPENBEAST_CHAT_OPERATORS", "victim@example.com")
    tcp = socket.socket()
    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp.bind(("127.0.0.1", 0))
    tcp.listen(128)
    port = tcp.getsockname()[1]
    _queue_reset_requests(port, 20)
    unix = chat_server._unix_listener(sock_path)
    app = chat_server.create_app()
    server = uvicorn.Server(chat_server._uvicorn_config(app, "127.0.0.1", port))
    t = threading.Thread(target=server.run, kwargs={"sockets": [tcp, unix]},
                         daemon=True)
    t.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not server.started:
            time.sleep(0.05)
        time.sleep(1.0)                 # let the queued connections drain
        rows = [r for r in rig.audit_rows()
                if r.get("login") == "victim@example.com"]
        forged = [r for r in rows if r.get("verified") or r.get("peer") == "unix"]
        assert not forged, f"a reset TCP peer was trusted: {forged[:3]}"
        if not rows:
            # The kernel/loop dropped every reset connection before a byte
            # was dispatched: nothing was exercised (the unit tests above
            # still pin the rule). Seen dispatched on Linux and macOS.
            pytest.skip("no reset request reached the app on this platform")
        # Control: the socket itself still carries the login.
        from test_chat_review import _uds_get
        code, body = _uds_get(sock_path, "/api/chat/sessions",
                              {**LOGIN, "Host": "127.0.0.1"})
        assert code == 200, body
    finally:
        server.should_exit = True
        t.join(10)
        import shutil
        shutil.rmtree(sock_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# SSE efficiency: idle polls do no I/O, replays are batched
# ---------------------------------------------------------------------------

def _dead_pid():
    import subprocess
    p = subprocess.Popen([sys.executable, "-c", ""])
    p.wait()
    return p.pid


def test_an_idle_stream_poll_reads_nothing_and_reconciles_once(rig, monkeypatch):
    """An attached console polls every POLL_MS. With nothing new it used to
    hop to the threadpool to read 0 bytes, then reconcile the record twice
    (sessions.get already reconciles). The record here is `running` with a
    dead pid, so the stream drains, sees `lost`, and idles through its grace
    window before closing — every pass of which is an idle poll."""
    sid = rig.session(kind="agent", state="running", pid=_dead_pid())
    reads, gets, recs = [], [], []
    real_read = chat_server.read_lines_from
    real_get = sessions.get
    real_rec = sessions.reconcile

    def read(path, offset):
        reads.append(offset)
        return real_read(path, offset)

    def get(session_id):
        gets.append(session_id)
        return real_get(session_id)

    def rec(r):
        recs.append(r.get("id"))
        return real_rec(r)
    monkeypatch.setattr(chat_server, "read_lines_from", read)
    monkeypatch.setattr(sessions, "get", get)
    monkeypatch.setattr(sessions, "reconcile", rec)
    r = rig.client.get(f"/api/chat/sessions/{sid}/events")
    assert r.status_code == 200
    from test_chat_server import parse_sse
    frames = parse_sse(r.text)
    assert frames[-1]["event"] == "end"
    assert len(gets) >= 5, "the stream did not idle through several polls"
    assert len(reads) == 1, f"idle polls still read the transcript: {reads}"
    # every reconcile is the one inside sessions.get: none on top of it
    assert len(recs) <= len(gets), (len(recs), len(gets))


def test_a_replay_is_written_in_chunks_not_line_by_line(rig):
    """read_lines_from returns up to 256 KB of complete lines; each line used
    to become its own ASGI body message (2-5x slower replays). The frames,
    their ids and their order must be exactly what they were."""
    lines = [{"type": "tool_call", "name": "bash", "result": f"r{i}"}
             for i in range(300)]
    sid = rig.session(kind="agent", state="done", lines=lines)
    url = f"/api/chat/sessions/{sid}/events"
    with rig.client.stream("GET", url) as r:
        assert r.status_code == 200
        chunks = [c for c in r.iter_raw() if c]
    from test_chat_server import parse_sse, line_offsets
    frames = parse_sse(b"".join(chunks).decode())
    body = [f for f in frames if f["event"] not in ("hello", "end")]
    assert len(body) == 300
    assert [f["id"] for f in body] == line_offsets(sessions.get(sid)["transcript"])
    assert len(chunks) < 20, f"{len(chunks)} body writes for 300 lines"


# ---------------------------------------------------------------------------
# Session list / health: one scan, one reconcile per row
# ---------------------------------------------------------------------------

def test_credentialed_health_scans_the_ledger_once(rig, monkeypatch):
    rig.session(kind="agent", state="running")          # this pid: alive
    rig.session(kind="job", state="done")
    rig.session(kind="job", state="running", pid=_dead_pid())   # -> lost
    scans = []
    real = sessions.list_sessions

    def counted(**kw):
        scans.append(kw)
        return real(**kw)
    rig.client                                     # build the app first
    monkeypatch.setattr(sessions, "list_sessions", counted)
    d = rig.client.get("/api/chat/health", headers=rig.local).json()
    assert len(scans) == 1, scans
    assert d["running"] == 1 and d["sessions"] == 3


def test_the_session_list_reconciles_each_row_once(rig, monkeypatch):
    live = [rig.session(kind="agent", state="running") for _ in range(3)]
    dead = rig.session(kind="job", state="running", pid=_dead_pid())
    calls = []
    real = sessions.reconcile

    def counted(r):
        calls.append(r.get("id"))
        return real(r)
    monkeypatch.setattr(sessions, "reconcile", counted)
    got = rig.client.get("/api/chat/sessions").json()["sessions"]
    assert sorted(calls) == sorted(live + [dead]), calls     # once each
    states = {s["id"]: s["state"] for s in got}
    assert states[dead] == "lost" and all(states[s] == "running" for s in live)
    # the state filter still excludes the row that reconciled to `lost`
    calls.clear()
    got = rig.client.get("/api/chat/sessions?state=running").json()["sessions"]
    assert sorted(s["id"] for s in got) == sorted(live)
