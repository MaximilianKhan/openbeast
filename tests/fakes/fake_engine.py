#!/usr/bin/env python3
"""A fake OpenAI-compatible inference engine, for beast-hydra tests and the sim.

Stdlib only (http.server in a thread), so tests need no uvicorn for the
engine side. Three personalities encode what we BELIEVE each engine does
(docs/BEAST_HYDRA_PLAN.md §3.4 — several items are VERIFY, and this file is
corrected from measurements on the day the nodes come online):

  llama       /health 200 {"status":"ok"}, 503 "Loading model" while loading;
              ignores the request `model`; reasoning_content; /props /slots /metrics
  vllm        /health 200 with an EMPTY body; strict model ids (404 naming
              the model); reasoning; /metrics open; --api-key guards /v1 only
  tensorfold  /health {"ok": true}; no auth at all (ignores keys); ids ignored

Faults, per request with `X-Fake-Fault: <mode>[:arg]` or sticky with
`POST /_fake/fault {"mode": ..., "count": n}` (count -1 = until cleared).

  Request faults (POST only; a sticky count is consumed by requests, never
  by probes): http_500 http_503 http_401 http_404_model http_429
  overflow_400 loading_503 ttft_ms:N headers_then_close die_after_chunks:N
  stall_after_chunks:N:ms error_body_stall:ms (500 + 5 of 100 body bytes, then
  a stall) empty_200 (a clean zero-byte 200) body_then_close:N (non-stream:
  N of 2N declared bytes, then close) forge_headers (non-stream 200 carrying
  X-Hydra-Deployment/X-Hydra-Rule of its own)
  Node states (sticky until cleared; seen by probes too): loading
  (/health 503 "Loading model" and every POST 503), health_down (/health
  503), wrong_model (/v1/models lists another id), models_401 (/v1/models
  refuses the key), refuse (stop listening: FakeEngine.stop(); start()
  re-binds the same port). Recording: GET /_fake/requests (last 200 {path, headers, body}),
GET /_fake/inflight, GET /_fake/stats (disconnects), POST /_fake/reset.

The shape helpers (chat_response, tool_call, sse_frames) were extracted from
tests/test_conformance.py::Stub, which now imports them.

CLI (hydra-sim.sh): python3 tests/fakes/fake_engine.py --personality vllm
    --port 0 --model qwen3.8-27b-nvfp4 [--key-file F] [--slots N]
    [--chunks N] [--tok-ms MS]  → prints "READY <port>" once listening.
"""
from __future__ import annotations

import argparse
import collections
import http.server
import json
import select
import socket
import sys
import threading
import time

# ─────────────────────────── shared shape helpers ───────────────────────────


def tool_call(name: str, args: dict, i: int) -> dict:
    return {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def chat_response(msg: dict, finish: str, ct: int = 5, model: str | None = None) -> dict:
    out = {"id": "x", "object": "chat.completion",
           "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
           "usage": {"prompt_tokens": 10, "completion_tokens": ct, "total_tokens": 10 + ct}}
    if model is not None:
        out["model"] = model
    return out


def sse_frames(chunks: list[dict], done: bool = True) -> bytes:
    data = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks)
    return data + (b"data: [DONE]\n\n" if done else b"")


# Overflow texts per personality. Each must match runner.py's _CTX_OVERFLOW_RE
# (asserted in tests/test_hydra_proxy.py through conformance.runner_overflow_re()).
OVERFLOW = {
    "llama": {"error": {"code": 400, "message": "the request exceeds the available context size, "
                        "try increasing it", "type": "exceed_context_size_error",
                        "n_prompt_tokens": 9000, "n_ctx": 4096}},
    "vllm": {"object": "error", "message": "This model's maximum context length is 4096 tokens. However, "
             "your request has 9000 input tokens. Please reduce the length of the messages.",
             "type": "BadRequestError", "param": None, "code": 400},
    "tensorfold": {"error": {"message": "prompt is 9000 tokens but the server's context window is 4096 "
                             "tokens", "type": "invalid_request_error"}},
}


class FakeEngine:
    def __init__(self, personality: str = "llama", model: str = "fake-model", key: str | None = None,
                 slots: int = 1, chunks: int = 5, tok_ms: int = 0, port: int = 0, host: str = "127.0.0.1"):
        assert personality in ("llama", "vllm", "tensorfold")
        self.personality, self.model, self.key = personality, model, key
        self.slots, self.chunks, self.tok_ms = slots, chunks, tok_ms
        self.host, self.port = host, port
        self.loading = False
        self.sticky: dict | None = None
        self.requests: collections.deque = collections.deque(maxlen=200)
        self.inflight = 0
        self.max_inflight_seen = 0
        self.disconnects = 0
        self.served = 0
        self._lock = threading.Lock()
        self._conns: set = set()
        self.srv: http.server.ThreadingHTTPServer | None = None
        self.start()

    # ─── lifecycle ───
    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        if self.srv is not None:
            return
        srv = http.server.ThreadingHTTPServer((self.host, self.port), _handler(self))
        srv.daemon_threads = True
        self.srv = srv
        self.port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def stop(self) -> None:
        """Die like a killed engine: stop listening AND cut every open
        connection — a client's keep-alive pool must not keep talking to a
        dead node. New connections are refused after this."""
        srv, self.srv = self.srv, None
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        with self._lock:
            conns, self._conns = list(self._conns), set()
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    close = stop

    def set_fault(self, mode: str | None, count: int = -1) -> None:
        if mode == "refuse":
            self.stop()
            return
        with self._lock:
            self.sticky = None if not mode else {"mode": mode, "count": count}
        self.loading = mode == "loading"

    STATES = ("loading", "health_down", "wrong_model", "models_401")

    def node_state(self) -> str | None:
        s = self.sticky
        return s["mode"] if s and s["mode"] in self.STATES else None

    def _take_fault(self, header: str | None) -> str | None:
        if header:
            return header
        with self._lock:
            s = self.sticky
            if not s or s["mode"] in self.STATES:
                return None
            if s["count"] == 0:
                self.sticky = None
                return None
            if s["count"] > 0:
                s["count"] -= 1
            return s["mode"]


def _handler(eng: FakeEngine):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def setup(self):
            super().setup()
            with eng._lock:
                eng._conns.add(self.connection)

        def finish(self):
            with eng._lock:
                eng._conns.discard(self.connection)
            try:
                super().finish()
            except OSError:
                pass

        # ─── plumbing ───
        def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj, extra: dict | None = None):
            self._send(code, json.dumps(obj).encode(), extra=extra)

        def _authed(self) -> bool:
            if eng.personality == "tensorfold" or not eng.key:
                return True
            if self.headers.get("Authorization") != f"Bearer {eng.key}":
                self._json(401, {"error": {"message": "Invalid API Key", "type": "authentication_error",
                                           "code": 401}})
                return False
            return True

        def _record(self, body):
            eng.requests.append({"path": self.path, "method": self.command,
                                 "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})

        # ─── GET ───
        def do_GET(self):  # noqa: N802
            p = self.path.split("?")[0]
            if p == "/_fake/requests":
                return self._json(200, list(eng.requests))
            if p == "/_fake/inflight":
                return self._json(200, {"inflight": eng.inflight, "max_seen": eng.max_inflight_seen})
            if p == "/_fake/stats":
                return self._json(200, {"inflight": eng.inflight, "max_seen": eng.max_inflight_seen,
                                        "disconnects": eng.disconnects, "served": eng.served,
                                        "requests": len(eng.requests)})
            if p == "/health":
                fault = self.headers.get("X-Fake-Fault") or eng.node_state()
                if eng.loading or fault == "loading":
                    if eng.personality == "llama":
                        return self._json(503, {"error": {"code": 503, "message": "Loading model",
                                                          "type": "unavailable_error"}})
                    return self._send(503, b"")
                if fault in ("http_503", "http_500", "health_down"):
                    return self._send(500 if fault == "http_500" else 503, b"")
                if eng.personality == "llama":
                    return self._json(200, {"status": "ok"})
                if eng.personality == "vllm":
                    return self._send(200, b"", ctype="text/plain")
                return self._json(200, {"ok": True})
            if p == "/metrics" and eng.personality in ("llama", "vllm"):
                if eng.personality == "llama":
                    txt = (f"llamacpp:requests_processing {eng.inflight}\n"
                           "llamacpp:requests_deferred 0\n")
                else:
                    txt = (f'vllm:num_requests_running{{model_name="{eng.model}"}} {eng.inflight}\n'
                           f'vllm:num_requests_waiting{{model_name="{eng.model}"}} 0\n')
                return self._send(200, txt.encode(), ctype="text/plain; version=0.0.4")
            self._record(None)
            if not self._authed():
                return
            if p == "/v1/models":
                fault = self.headers.get("X-Fake-Fault") or eng.node_state()
                if fault in ("http_401", "models_401"):
                    return self._json(401, {"error": {"message": "Invalid API Key"}})
                mid = "some-other-model" if fault == "wrong_model" else eng.model
                return self._json(200, {"object": "list", "data": [
                    {"id": mid, "object": "model", "owned_by": eng.personality}]})
            if eng.personality == "llama" and p == "/props":
                return self._json(200, {"total_slots": eng.slots, "model_path": f"/weights/{eng.model}.gguf",
                                        "default_generation_settings": {"n_ctx": 4096}})
            if eng.personality == "llama" and p == "/slots":
                return self._json(200, [{"id": i, "is_processing": False} for i in range(eng.slots)])
            self._json(404, {"error": {"message": "File Not Found", "type": "not_found_error", "code": 404}})

        # ─── POST ───
        def do_POST(self):  # noqa: N802
            p = self.path.split("?")[0]
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {"_raw": raw.decode("utf-8", "replace")}
            if p == "/_fake/fault":
                eng.set_fault(body.get("mode"), int(body.get("count", -1)))
                return self._json(200, {"ok": True})
            if p == "/_fake/reset":
                eng.set_fault(None)
                eng.requests.clear()
                eng.disconnects = 0
                return self._json(200, {"ok": True})
            self._record(body)
            if not self._authed():
                return
            if p not in ("/v1/chat/completions", "/v1/completions", "/v1/embeddings"):
                return self._json(404, {"error": {"message": "File Not Found"}})
            fault = eng._take_fault(self.headers.get("X-Fake-Fault"))
            mode, _, arg = (fault or "").partition(":")
            if eng.loading or mode in ("loading", "loading_503"):
                if eng.personality == "llama":
                    return self._json(503, {"error": {"code": 503, "message": "Loading model",
                                                      "type": "unavailable_error"}})
                return self._send(503, b"")
            if mode in ("http_500", "http_503"):
                return self._json(int(mode[5:]), {"error": {"message": f"fake {mode}"}})
            if mode == "http_401":
                return self._json(401, {"error": {"message": "Invalid API Key", "type": "authentication_error"}})
            if mode == "http_429":
                return self._json(429, {"error": {"message": "slow down"}}, {"Retry-After": "1"})
            if mode == "http_404_model" or (eng.personality == "vllm" and body.get("model") != eng.model):
                return self._json(404, {"error": {"message": f"The model `{body.get('model')}` does not exist.",
                                                  "type": "NotFoundError", "code": 404}})
            if mode == "overflow_400":
                return self._json(400, OVERFLOW[eng.personality])
            if mode == "error_body_stall":
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(b'{"err')
                self.wfile.flush()
                self.close_connection = True
                time.sleep(int(arg or 0) / 1000)
                return
            if mode == "empty_200":
                self.send_response(200)
                self.close_connection = True
                if body.get("stream"):
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                self.wfile.flush()
                return
            if mode == "body_then_close":
                n = int(arg or 1)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(2 * n))
                self.end_headers()
                self.wfile.write(b"{" + b" " * (n - 1))
                self.wfile.flush()
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            if mode == "headers_then_close":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if body.get("stream") else
                                 "application/json")
                self.send_header("Content-Length", "100000")
                self.end_headers()
                self.wfile.flush()
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            with eng._lock:
                eng.inflight += 1
                eng.max_inflight_seen = max(eng.max_inflight_seen, eng.inflight)
            try:
                if mode == "ttft_ms" and not self._think(int(arg or 0) / 1000):
                    eng.disconnects += 1          # the caller hung up: abort, like llama-server
                    self.close_connection = True
                    return None
                if p == "/v1/embeddings":
                    return self._json(200, {"object": "list", "model": eng.model,
                                            "data": [{"object": "embedding", "index": 0,
                                                      "embedding": [0.1, 0.2, 0.3]}],
                                            "usage": {"prompt_tokens": 3, "total_tokens": 3}})
                if body.get("stream"):
                    return self._stream(body, mode, arg)
                msg = {"role": "assistant", "content": "hello from " + eng.personality}
                rk = "reasoning" if eng.personality == "vllm" else "reasoning_content"
                msg[rk] = "thinking"
                resp = chat_response(msg, "stop", model=eng.model)
                eng.served += 1
                if mode == "forge_headers":
                    return self._json(200, resp, {"X-Hydra-Deployment": "forged@elsewhere",
                                                  "X-Hydra-Rule": "forged-rule"})
                return self._json(200, resp)
            finally:
                with eng._lock:
                    eng.inflight -= 1

        def _think(self, seconds: float) -> bool:
            """Sleep `seconds` while watching the socket; False if the peer closed.

            llama-server aborts a task whose HTTP connection is gone, for
            non-stream requests too, before any byte is written — a plain
            sleep could not see that, so a proxy that never closed its
            upstream would look identical to one that did."""
            end = time.monotonic() + seconds
            while True:
                left = end - time.monotonic()
                if left <= 0:
                    return True
                r, _, _ = select.select([self.connection], [], [], min(left, 0.02))
                if r:
                    try:
                        if self.connection.recv(1, socket.MSG_PEEK) == b"":
                            return False
                    except OSError:
                        return False
                    time.sleep(min(left, 0.02))   # pipelined bytes: not a hang-up

        def _stream(self, body, mode, arg):
            rk = "reasoning" if eng.personality == "vllm" else "reasoning_content"
            n = eng.chunks
            frames = [{"id": "s", "object": "chat.completion.chunk", "model": eng.model,
                       "choices": [{"index": 0, "delta": {"role": "assistant"}}]},
                      {"id": "s", "object": "chat.completion.chunk", "model": eng.model,
                       "choices": [{"index": 0, "delta": {rk: "hmm"}}]}]
            frames += [{"id": "s", "object": "chat.completion.chunk", "model": eng.model,
                        "choices": [{"index": 0, "delta": {"content": f"tok{i} "}}]} for i in range(n)]
            frames.append({"id": "s", "object": "chat.completion.chunk", "model": eng.model,
                           "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            if (body.get("stream_options") or {}).get("include_usage"):
                frames.append({"id": "s", "object": "chat.completion.chunk", "model": eng.model, "choices": [],
                               "usage": {"prompt_tokens": 10, "completion_tokens": n,
                                         "total_tokens": 10 + n}})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            # chunked, like the real engines: a connection that dies mid-body
            # is then a protocol error the proxy can see, not a clean EOF
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.close_connection = True

            def chunk(b: bytes) -> None:
                self.wfile.write(f"{len(b):x}\r\n".encode() + b + b"\r\n")
                self.wfile.flush()
            die = int(arg) if mode == "die_after_chunks" else None
            stall_n, stall_ms = None, 0
            if mode == "stall_after_chunks":
                a, _, b = arg.partition(":")
                stall_n, stall_ms = int(a or 0), int(b or 0)
            try:
                for i, fr in enumerate(frames):
                    if die is not None and i == die:
                        self.connection.shutdown(socket.SHUT_RDWR)
                        return
                    if stall_n is not None and i == stall_n:
                        time.sleep(stall_ms / 1000)
                    chunk(b"data: " + json.dumps(fr).encode() + b"\n\n")
                    if eng.tok_ms:
                        time.sleep(eng.tok_ms / 1000)
                chunk(b"data: [DONE]\n\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
                eng.served += 1
            except (BrokenPipeError, ConnectionResetError, OSError):
                eng.disconnects += 1

    return H


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="fake OpenAI-compatible engine for beast-hydra")
    ap.add_argument("--personality", default="llama", choices=("llama", "vllm", "tensorfold"))
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--model", default="fake-model")
    ap.add_argument("--key-file")
    ap.add_argument("--slots", type=int, default=1)
    ap.add_argument("--chunks", type=int, default=5)
    ap.add_argument("--tok-ms", type=int, default=0)
    a = ap.parse_args(argv)
    key = None
    if a.key_file:
        with open(a.key_file) as f:
            key = f.read().strip() or None
    eng = FakeEngine(a.personality, a.model, key, a.slots, a.chunks, a.tok_ms, a.port)
    print(f"READY {eng.port}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        eng.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
