"""scripts/tokps.py: tok/s measurement and the engine gate (docs/WEIGHT_STREAMING.md).

A loopback stub plays llama-server (a stream whose last chunk carries
`timings`) and a generic OpenAI server (no timings: the rate comes from the
stream itself). No GPU, no network.
"""
import importlib.util
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("tokps", os.path.join(HERE, "..", "scripts", "tokps.py"))
tokps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tokps)


class _Stub:
    """mode: 'llama' (timings in the final chunk) or 'openai' (no timings)."""

    def __init__(self, mode, tps=40.0, key=""):
        self.mode, self.tps, self.key, self.bodies, self.auth = mode, tps, key, [], []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _deny(self):
                if stub.key and self.headers.get("Authorization") != "Bearer " + stub.key:
                    self.send_response(401)
                    self.end_headers()
                    return True
                return False

            def do_GET(self):
                if self._deny():
                    return
                body = json.dumps({"data": [{"id": "stub-model"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                stub.auth.append(self.headers.get("Authorization"))
                if self._deny():
                    return
                n = int(self.headers.get("Content-Length") or 0)
                stub.bodies.append(json.loads(self.rfile.read(n)))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                ntok = 12
                for i in range(ntok):
                    ev = {"choices": [{"delta": {"content": f"t{i} "}}]}
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(1.0 / stub.tps)
                last = {"choices": [], "usage": {"completion_tokens": ntok, "prompt_tokens": 30}}
                if stub.mode == "llama":
                    last["timings"] = {"predicted_per_second": stub.tps, "prompt_per_second": 812.5,
                                       "predicted_n": ntok, "prompt_n": 30}
                self.wfile.write(f"data: {json.dumps(last)}\n\ndata: [DONE]\n\n".encode())

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def stub(request):
    made = []

    def make(*a, **k):
        s = _Stub(*a, **k)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def test_llama_timings_are_the_decode_rate(stub, tmp_path):
    s = stub("llama", tps=67.8)
    rec = tmp_path / "r.json"
    assert tokps.main(["measure", "--url", s.url, "--runs", "2", "--record", str(rec)]) == 0
    d = json.loads(rec.read_text())
    assert d["decode_tps_median"] == 67.8 and d["prefill_tps_median"] == 812.5
    assert d["model"] == "stub-model" and len(d["runs"]) == 3          # warm-up + 2
    assert all(r["source"] == "server-timings" for r in d["runs"])
    b = s.bodies[0]
    assert b["temperature"] == 0 and b["seed"] == tokps.SEED and b["stream"] is True


def test_generic_server_rate_from_the_stream_excludes_ttft(stub, tmp_path):
    s = stub("openai", tps=50.0)
    rec = tmp_path / "r.json"
    assert tokps.main(["measure", "--url", s.url + "/v1", "--runs", "1", "--record", str(rec)]) == 0
    d = json.loads(rec.read_text())
    assert d["runs"][1]["source"] == "stream" and d["prefill_tps_median"] is None
    # 11 gaps of ~20 ms: close to 50 tok/s, never the wall-clock rate.
    assert 25 < d["decode_tps_median"] < 60


def test_check_enforces_the_floor(stub):
    s = stub("llama", tps=38.0)
    assert tokps.main(["check", "--url", s.url, "--runs", "1", "--min", "55"]) == 1
    assert tokps.main(["check", "--url", s.url, "--runs", "1", "--min", "30"]) == 0


def test_key_from_the_environment_never_argv(stub, monkeypatch):
    s = stub("llama", key="sekrit")
    monkeypatch.delenv("LLAMA_API_KEY", raising=False)
    with pytest.raises(SystemExit) as e:
        tokps.main(["measure", "--url", s.url, "--runs", "1"])
    assert e.value.code == 3
    monkeypatch.setenv("LLAMA_API_KEY", "sekrit")
    assert tokps.main(["measure", "--url", s.url, "--runs", "1"]) == 0
    assert "Bearer sekrit" in s.auth
    with pytest.raises(SystemExit):
        tokps.main(["measure", "--url", s.url, "--api-key", "sekrit"])   # no such flag


def _rec(tmp_path, name, tps, **kw):
    d = {"schema": "openbeast-tokps/1", "decode_tps_median": tps, "prompt": "short",
         "max_tokens": 256, "host": "beast", "label": name}
    d.update(kw)
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(d))
    return str(p)


def test_gate_passes_only_at_the_ratio(tmp_path):
    base = _rec(tmp_path, "llama", 20.0)
    out = tmp_path / "gate" / "maya.json"
    assert tokps.main(["gate", "--engine", "maya", "--candidate", _rec(tmp_path, "m1", 29.0),
                       "--baseline", base, "--out", str(out)]) == 1
    v = json.loads(out.read_text())
    assert v["pass"] is False and v["ratio"] == 1.45 and v["required"] == 1.5
    assert tokps.main(["gate", "--engine", "maya", "--candidate", _rec(tmp_path, "m2", 30.0),
                       "--baseline", base, "--out", str(out)]) == 0
    assert json.loads(out.read_text())["pass"] is True


def test_gate_refuses_apples_to_oranges(tmp_path):
    base = _rec(tmp_path, "llama", 20.0)
    assert tokps.main(["gate", "--engine", "maya", "--baseline", base,
                       "--candidate", _rec(tmp_path, "far", 90.0, host="elsewhere")]) == 1
    assert tokps.main(["gate", "--engine", "maya", "--baseline", base,
                       "--candidate", _rec(tmp_path, "long", 90.0, prompt="long")]) == 1


def test_gate_rejects_a_file_that_is_not_a_record(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"decode": 3}')
    with pytest.raises(SystemExit) as e:
        tokps.main(["gate", "--engine", "maya", "--candidate", str(bad), "--baseline", str(bad)])
    assert e.value.code == 2
