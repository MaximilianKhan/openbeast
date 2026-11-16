#!/usr/bin/env python3
"""The authenticating front of a LOCAL Open-Jev-27B host (scripts/serve-openjev.sh).

The Open-Jev loader (jev.server) has no authentication and serves a demo
workbench; it runs inside its container, published on the host's loopback
only. This gate is what beast-instinct's `openjev_head` binding talks to:

  * every route but GET /health needs `Authorization: Bearer <key>`; the key
    is read from a 0600 file (group/world-readable refuses to start), never
    from argv;
  * a path allowlist: GET /health, GET /v1/identity, POST /v1/systemone —
    nothing else of the loader (workbench, examples, other APIs) is reachable;
  * GET /v1/identity answers the pins serve-openjev.sh VERIFIED before start
    (base repo + revision, adapter revision, head/adapter sha256, image
    digest), so the binding's conformance probe can refuse a different head
    served under the same name;
  * bodies are capped (1 MiB) and never logged;
  * a tailnet peer without the key cannot pin the gate: an idle or
    half-sent request is dropped after REQUEST_TIMEOUT_S, at most
    MAX_CONNS connections are served at once (the rest get a 503), and the
    unauthenticated GET /health is answered from a HEALTH_TTL_S cache.

stdlib only (it runs on the GPU host beside the container, outside it).
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 1024 * 1024
UPSTREAM_TIMEOUT_S = 30.0
REQUEST_TIMEOUT_S = 10.0     # per socket op: a slowloris peer loses its thread
MAX_CONNS = 64               # concurrent connections; beyond it, 503 + close
HEALTH_TTL_S = 5.0           # unauthenticated /health hits the loader at most this often
HEALTH_UPSTREAM_TIMEOUT_S = 5.0


def read_key(path: str) -> str:
    st = os.stat(path)
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(f"{path} is group/world accessible (want 0600)")
    with open(path) as fh:
        key = fh.read().strip()
    if len(key) < 16:
        raise ValueError(f"{path}: key too short")
    return key


def make_handler(key: str, upstream: str, identity: dict,
                 request_timeout: float = REQUEST_TIMEOUT_S):
    want = f"Bearer {key}".encode()
    upstream = upstream.rstrip("/")
    health = {"at": None, "ok": False}
    health_lock = threading.Lock()

    def health_ok(fetch) -> bool:
        with health_lock:    # single flight: a /health flood is ONE upstream call
            now = time.monotonic()
            if health["at"] is None or now - health["at"] >= HEALTH_TTL_S:
                health["ok"] = fetch() == 200
                health["at"] = time.monotonic()
            return health["ok"]

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = request_timeout    # BaseHTTPRequestHandler's default None = forever

        def log_message(self, *a):   # never log bodies, prompts or keys
            pass

        def _send(self, code: int, obj) -> None:
            data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _authed(self) -> bool:
            got = self.headers.get("Authorization", "").encode()
            if hmac.compare_digest(got, want):
                return True
            self._send(401, {"error": "unauthorized"})
            return False

        def _upstream(self, method: str, path: str, body: bytes | None = None,
                      timeout: float = UPSTREAM_TIMEOUT_S):
            req = urllib.request.Request(upstream + path, data=body, method=method,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()
            except (urllib.error.URLError, OSError):
                return 502, json.dumps({"error": "open-jev loader unreachable"}).encode()

        def do_GET(self):
            if self.path == "/health":
                ok = health_ok(lambda: self._upstream(
                    "GET", "/health", timeout=HEALTH_UPSTREAM_TIMEOUT_S)[0])
                return self._send(200 if ok else 503, {"ok": ok})
            if not self._authed():
                return
            if self.path == "/v1/identity":
                return self._send(200, identity)
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._authed():
                return
            if self.path != "/v1/systemone":
                return self._send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 1 or n > MAX_BODY:
                return self._send(413, {"error": "body size outside limits"})
            body = self.rfile.read(n)
            code, data = self._upstream("POST", "/v1/systemone", body)
            self._send(code, data)
    return H


class _BoundedServer(ThreadingHTTPServer):
    """ThreadingHTTPServer starts one thread per connection with no cap; this
    one serves at most `max_conns` at once and answers the rest 503 + close
    without a thread, so idle connections cannot exhaust the host."""
    daemon_threads = True

    def __init__(self, addr, handler, max_conns: int):
        self._slots = threading.BoundedSemaphore(max(1, max_conns))
        super().__init__(addr, handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n"
                                b"Connection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def serve(host: str, port: int, key: str, upstream: str, identity: dict, *,
          request_timeout: float = REQUEST_TIMEOUT_S,
          max_conns: int = MAX_CONNS) -> ThreadingHTTPServer:
    if host in ("0.0.0.0", "::", ""):
        raise SystemExit("openjev_gate: refuse to listen on every interface; name one address")
    return _BoundedServer((host, port), make_handler(key, upstream, identity, request_timeout),
                          max_conns)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--listen", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--upstream", required=True, help="the loader, on this host's loopback")
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--identity", required=True, help="JSON written by serve-openjev.sh")
    a = ap.parse_args(argv)
    if not a.upstream.startswith(("http://127.0.0.1:", "http://localhost:")):
        print("openjev_gate: the upstream must be this host's loopback", file=sys.stderr)
        return 2
    try:
        key = read_key(a.key_file)
        with open(a.identity) as fh:
            identity = json.load(fh)
    except (OSError, ValueError, PermissionError) as exc:
        print(f"openjev_gate: refusing to start: {exc}", file=sys.stderr)
        return 3
    srv = serve(a.listen, a.port, key, a.upstream, identity)
    print(f"READY {a.listen}:{srv.server_address[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
