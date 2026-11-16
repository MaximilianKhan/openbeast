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
