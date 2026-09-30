"""A real beast-artifact server (and a `tailscale serve` stand-in) for tests.

Shared by tests/test_artifact_cli_live.py and tests/test_artifact_browser.py.
Everything runs IN THIS PROCESS on ephemeral loopback ports, in threads, and
is torn down by the context manager — no leaked servers, nothing on a fixed
port, never the rig's own :3004.

`IdentityProxy` mimics `tailscale serve`: it strips any client-supplied
Tailscale-User-Login, sets its own, and dials the upstream from 127.0.0.1 —
the only way a real browser can present a tailnet identity to the server.
"""
from __future__ import annotations

import http.client
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agents"))


def _bound_socket() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(64)
    return s


class LiveServer:
    """uvicorn serving artifact_server.create_app() on an ephemeral port."""

    def __init__(self):
        import uvicorn
        import artifact_server
        self.sock = _bound_socket()
        self.port = self.sock.getsockname()[1]
        self.app = artifact_server.create_app()
        self.token = self.app.state.local_token
        cfg = artifact_server._uvicorn_config(self.app, "127.0.0.1", self.port)
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.sock]},
            name="artifact-live", daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                c = http.client.HTTPConnection("127.0.0.1", self.port,
                                               timeout=1)
                c.request("GET", "/api/artifacts/health")
                if c.getresponse().status == 200:
                    return self
            except OSError:
                pass
            time.sleep(0.05)
        raise RuntimeError("artifact server did not come up")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        try:
            self.sock.close()
        except OSError:
            pass


class IdentityProxy:
    """A `tailscale serve` stand-in in front of a LiveServer."""

    def __init__(self, upstream_port: int, login: str):
        up, who = upstream_port, login

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else None
                hdrs = {k: v for k, v in self.headers.items()
                        if k.lower() not in ("tailscale-user-login",
                                             "connection", "keep-alive",
                                             "accept-encoding")}
                hdrs["Tailscale-User-Login"] = who
                c = http.client.HTTPConnection("127.0.0.1", up, timeout=30)
                try:
                    c.request(self.command, self.path, body=body,
                              headers=hdrs)
                    r = c.getresponse()
                    data = r.read()
                finally:
                    c.close()
                self.send_response(r.status)
                for k, v in r.getheaders():
                    if k.lower() in ("transfer-encoding", "connection",
                                     "content-length"):
                        continue
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            do_GET = do_POST = do_PATCH = do_DELETE = do_HEAD = _do

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       name="ts-proxy", daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
