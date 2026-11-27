"""Production HTTP adapter: ``python -m shopd.server``.

A fixed pool of worker threads named ``w1``..``wN`` pulls accepted
connections off a queue and runs ``App.handle`` for each request. A worker
that has served ``max_requests`` requests is retired and replaced by a fresh
thread with the same name (the log shows ``server.recycle``); this bounds
the damage of any per-thread state that grows over time.

The tests never import this module.
"""
from __future__ import annotations

import http.server
import os
import queue
import socketserver
import threading

from .app import create_app
from .config import Config
from .http import STATUS_TEXT, Headers, Request

DEFAULT_WORKERS = 4
DEFAULT_MAX_REQUESTS = 150


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "shopd"
    app = None  # set by serve()

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        req = Request.build(self.command, self.path, headers=Headers(self.headers.items()),
                            body=body, client=self.client_address[0])
        resp = self.app.handle(req)
        self.send_response(resp.status, STATUS_TEXT.get(resp.status))
        for k, v in resp.headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp.body)))
        self.end_headers()
        self.wfile.write(resp.body)

    do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = _serve

    def log_message(self, *_args):  # the app writes its own access log
        pass


class WorkerPool(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, addr, handler, workers: int, max_requests: int, logger):
        super().__init__(addr, handler)
        self.jobs: "queue.Queue" = queue.Queue()
        self.max_requests = max_requests
        self.logger = logger
        for i in range(1, workers + 1):
            self._spawn("w%d" % i)

    def _spawn(self, name: str) -> None:
        threading.Thread(target=self._work, name=name, daemon=True).start()
        self.logger.info("server.worker_start", worker=name)

    def _work(self) -> None:
        served = 0
        while True:
            request, client_address = self.jobs.get()
            try:
                self.finish_request(request, client_address)
            finally:
                self.shutdown_request(request)
            served += 1
            if served >= self.max_requests:
                name = threading.current_thread().name
                self.logger.info("server.recycle", worker=name, served=served)
                self._spawn(name)
                return

    def process_request(self, request, client_address):
        self.jobs.put((request, client_address))


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    config = Config.from_env(os.environ)
    app = create_app(config=config)
    _Handler.app = app
    workers = int(os.environ.get("SHOPD_WORKERS", DEFAULT_WORKERS))
    max_requests = int(os.environ.get("SHOPD_MAX_REQUESTS", DEFAULT_MAX_REQUESTS))
    with WorkerPool((host, port), _Handler, workers, max_requests, app.logger) as srv:
        srv.serve_forever()


if __name__ == "__main__":
    serve(port=int(os.environ.get("PORT", "8000")))
