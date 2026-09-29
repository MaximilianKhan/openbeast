"""scripts/backends/conformance.sh against stub OpenAI servers that behave like
vLLM (strict ids, `reasoning`, structured calls), TensorFold (ids ignored,
`reasoning_content`, string-valued tool arguments, one call per turn) and a
broken server (tool calls left as text). Each stub is an http.server on an
ephemeral 127.0.0.1 port, shut down by the test that started it. No network.
"""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PYLIB = REPO / "scripts" / "backends" / "pylib"
sys.path.insert(0, str(PYLIB))

import conformance  # noqa: E402

MARK = conformance.MARKER_TEXT


class Stub:
    def __init__(self, kind: str, key: str | None = None):
        self.kind, self.key = kind, key
        self.auth: list[str | None] = []
        self.requests: list[dict] = []
        stub = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _authed(self) -> bool:
                h = self.headers.get("Authorization")
                stub.auth.append(h)
                if stub.key and h != f"Bearer {stub.key}":
                    self._json(401, {"error": {"message": "Unauthorized"}})
                    return False
                return True

            def do_GET(self):  # noqa: N802
                if not self._authed():
                    return
                if self.path == "/v1/models":
                    if stub.kind in ("tensorfold",):
                        return self._json(200, {"object": "list", "data": [{"id": "local-model"}]})
                    return self._json(200, {"object": "list", "data": [
                        {"id": "brand-new", "object": "model", "max_model_len": 4096}]})
                self._json(404, {"error": "?"})

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if not self._authed():
                    return
                stub.requests.append(body)
                ans = stub.answer(body)
                if ans is None:
                    return self._sse(body)
                self._json(*ans)

            def _sse(self, body):
                reason_key = "reasoning" if stub.kind == "vllm" else "reasoning_content"
                chunks = [{"choices": [{"index": 0, "delta": {"role": "assistant"}}]},
                          {"choices": [{"index": 0, "delta": {reason_key: "the sea…"}}]},
                          {"choices": [{"index": 0, "delta": {"content": "The sea is wide."}}]},
                          {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
                data = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    # -------------------------------------------------------------- behaviour
    def answer(self, body: dict):
        k = self.kind
        if k in ("vllm", "broken", "objargs") and body.get("model") != "brand-new":
            return 404, {"error": {"message": f"The model `{body.get('model')}` does not exist.",
                                   "type": "NotFoundError", "code": 404}}
        msgs = body.get("messages") or []
        text = "".join(str(m.get("content") or "") for m in msgs)
        if len(text) > 10000:
            if k == "vllm":
                return 400, {"error": {"message": "This model's maximum context length is 4096 tokens. However, "
                                                  "your request has 9000 input tokens.", "code": 400}}
            return 400, {"error": {"message": "prompt too long"}}
        if body.get("stream"):
            return None
        if body.get("max_tokens", 9999) <= 16:
            return 200, self._resp({"role": "assistant", "content": "1 2 3 4 5 6 7"}, "length",
                                   body["max_tokens"])
        if body.get("tools"):
            if msgs[-1].get("role") == "tool":
                return 200, self._resp({"role": "assistant", "content": f"The command printed {MARK}."}, "stop")
            if "Read BOTH files" in text:
                calls = [self._call("read_file", {"path": "/etc/hostname"}, 0)]
                if k == "vllm":
                    calls.append(self._call("read_file", {"path": "/etc/os-release"}, 1))
                return 200, self._resp({"role": "assistant", "content": None, "tool_calls": calls}, "tool_calls")
            if k == "broken":
                return 200, self._resp({"role": "assistant", "content":
                                        '<tool_call>{"name": "bash", "arguments": {"command": "echo x"}}</tool_call>'},
                                       "stop")
            args = {"command": f"echo {MARK}"}
            if k == "tensorfold":
                args["timeout"] = "30"                        # XML parameter values arrive as strings
            call = self._call("bash", args, 0)
            if k == "objargs":
                call["function"]["arguments"] = args
            return 200, self._resp({"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls")
        msg = {"role": "assistant", "content": "391"}
        if k == "vllm":
            msg["reasoning"] = "17 * 23 = 391"
        elif k == "tensorfold":
            msg["reasoning_content"] = "17 * 23 = 391"
        else:
            msg["content"] = "<think>17*23</think>391"
        return 200, self._resp(msg, "stop")

    @staticmethod
    def _call(name, args, i):
        return {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}

    @staticmethod
    def _resp(msg, finish, ct=5):
        return {"id": "x", "object": "chat.completion", "choices": [{"index": 0, "message": msg,
                                                                      "finish_reason": finish}],
                "usage": {"prompt_tokens": 10, "completion_tokens": ct, "total_tokens": 10 + ct}}


@pytest.fixture
def stub_factory():
    made = []

    def make(kind, key=None):
        s = Stub(kind, key)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("LLAMA_API_KEY", "OPENBEAST_API_KEY", "OPENBEAST_INFERENCE_URL", "INFERENCE_URL",
              "OPENBEAST_INFERENCE_BACKEND", "INFERENCE_BACKEND", "http_proxy", "HTTP_PROXY"):
        monkeypatch.delenv(k, raising=False)


def run(tmp_path, url, *args) -> tuple[int, dict]:
    out = tmp_path / "reports"
    rc = conformance.main(["--url", url, "--out", str(out), "--timeout", "20", *args])
    return rc, json.loads((out / "latest.json").read_text())


def by_name(doc) -> dict:
    return {r["name"]: r for r in doc["results"]}


def test_vllm_like_server_passes(tmp_path, stub_factory):
    s = stub_factory("vllm")
    rc, doc = run(tmp_path, s.url, "--backend", "vllm", "--heavy")
    r = by_name(doc)
    assert rc == 0 and doc["ok"]
    assert r["models"]["status"] == "pass" and doc["facts"]["max_model_len"] == 4096
    assert doc["facts"]["strict_model_names"] is True and r["unknown_id"]["status"] == "info"
    assert r["chat"]["status"] == r["stream"]["status"] == "pass"
    assert r["reasoning"]["status"] == "warn" and doc["facts"]["reasoning_field"] == "reasoning"
    assert r["tools"]["status"] == "pass" and r["tool_result"]["status"] == "pass"
    assert r["parallel"]["status"] == "pass" and r["max_tokens"]["status"] == "pass"
    assert r["overflow"]["status"] == "pass", r["overflow"]["detail"]   # runner.py's own regex matched it
    assert any("Strict model names" in x for x in doc["recommendations"])
    # it probed with OpenBeast's real tool schemas
    tools_req = next(b for b in s.requests if b.get("tools"))
    assert {t["function"]["name"] for t in tools_req["tools"]} == {"bash", "read_file"}
    assert doc["facts"]["tool_schemas_from"] == "agents/tools.py TOOL_SCHEMAS"
    # the tool result went back with the call's id
    follow = next(b for b in s.requests if (b.get("messages") or [{}])[-1].get("role") == "tool")
    assert follow["messages"][-1]["tool_call_id"] == "call_0"
    assert list((tmp_path / "reports").glob("conformance-*-brand-new.txt"))


def test_tensorfold_like_server_passes_with_caveats(tmp_path, stub_factory, monkeypatch):
    s = stub_factory("tensorfold")
    monkeypatch.setenv("LLAMA_API_KEY", "sk-should-not-travel")
    rc, doc = run(tmp_path, s.url, "--backend", "tensorfold", "--concurrency", "3")
    r = by_name(doc)
    assert rc == 0
    assert doc["facts"]["strict_model_names"] is False
    assert r["reasoning"]["status"] == "pass" and doc["facts"]["reasoning_field"] == "reasoning_content"
    assert r["tools"]["status"] == "pass" and "STRING" in r["tools"]["detail"]
    assert doc["facts"]["tool_arguments_string_values"] is True
    assert r["parallel"]["status"] == "warn" and r["concurrency"]["status"] == "pass"
    assert set(s.auth) == {None}, "the key must never be sent to TensorFold"
    assert any("arrive as strings" in x for x in doc["recommendations"])


def test_broken_server_fails_required_probes(tmp_path, stub_factory):
    s = stub_factory("broken")
    rc, doc = run(tmp_path, s.url, "--heavy")
    r = by_name(doc)
    assert rc == 1 and not doc["ok"]
    assert r["tools"]["status"] == "fail" and "as TEXT" in r["tools"]["detail"]
    assert r["tool_result"]["status"] == "skip"
    assert r["reasoning"]["status"] == "warn" and "INLINE" in r["reasoning"]["detail"]
    assert r["overflow"]["status"] == "fail" and "NOT recognised" in r["overflow"]["detail"]
    assert r["chat"]["status"] == "pass"


def test_object_arguments_fail(tmp_path, stub_factory):
    rc, doc = run(tmp_path, stub_factory("objargs").url)
    assert rc == 1 and "not a string" in by_name(doc)["tools"]["detail"]


def test_api_key_from_env_or_file_never_argv(tmp_path, stub_factory, monkeypatch):
    s = stub_factory("vllm", key="k-1")
    rc, doc = run(tmp_path, s.url, "--backend", "vllm")
    assert rc == 1 and "wants a key" in by_name(doc)["models"]["detail"]
    monkeypatch.setenv("LLAMA_API_KEY", "k-1")
    assert run(tmp_path, s.url, "--backend", "vllm")[0] == 0
    monkeypatch.delenv("LLAMA_API_KEY")
    kf = tmp_path / "key"
    kf.write_text("k-1\n")
    kf.chmod(0o600)
    assert run(tmp_path, s.url, "--backend", "vllm", "--key-file", str(kf))[0] == 0
    kf.chmod(0o644)
    with pytest.raises(SystemExit, match="chmod 600"):
        run(tmp_path, s.url, "--key-file", str(kf))


def test_unreachable_and_usage(tmp_path):
    import socket

    so = socket.socket()
    so.bind(("127.0.0.1", 0))
    port = so.getsockname()[1]
    so.close()
    rc, doc = run(tmp_path, f"http://127.0.0.1:{port}")
    assert rc == 1 and by_name(doc)["models"]["status"] == "fail"
    assert conformance.main(["--out", str(tmp_path)]) == 2


def test_cli_exit_codes_and_json(tmp_path, stub_factory):
    good, bad = stub_factory("vllm"), stub_factory("broken")
    env = {k: v for k, v in os.environ.items() if k not in ("LLAMA_API_KEY", "OPENBEAST_API_KEY")}
    cmd = [sys.executable, str(PYLIB / "conformance.py"), "--out", str(tmp_path / "o"), "--json"]
    a = subprocess.run(cmd + ["--url", good.url], capture_output=True, text=True, timeout=120, env=env)
    b = subprocess.run(cmd + ["--url", bad.url + "/v1"], capture_output=True, text=True, timeout=120, env=env)
    assert a.returncode == 0 and json.loads(a.stdout)["ok"] is True
    assert b.returncode == 1 and json.loads(b.stdout)["ok"] is False


def test_runner_overflow_regex_is_the_runners():
    rx = conformance.runner_overflow_re()
    assert rx is not None
    assert rx.search("This model's maximum context length is 4096 tokens")
    assert not rx.search("prompt too long")
