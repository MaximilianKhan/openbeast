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

    def __init__(self, mode, tps=40.0, key="", prompt_ns=None):
        self.mode, self.tps, self.key, self.bodies, self.auth = mode, tps, key, [], []
        self.prompt_ns = list(prompt_ns or [])   # timings.prompt_n per request (default 30)
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
                    pn = stub.prompt_ns.pop(0) if stub.prompt_ns else 30
                    last["timings"] = {"predicted_per_second": stub.tps,
                                       "prompt_per_second": 812.5 if pn > 1 else 9999.0,
                                       "predicted_n": ntok, "prompt_n": pn}
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


def test_no_counted_run_replays_another(stub):
    """Every request disables the prompt cache, and each run's prompt differs
    from the others within its first tokens (a header first, then question i
    of a fixed rotation): no prefix reuse, no replayed expert trace."""
    s = stub("llama", tps=40.0)
    assert tokps.main(["measure", "--url", s.url, "--runs", "3", "--prompt", "long"]) == 0
    prompts = [b["messages"][0]["content"] for b in s.bodies]
    assert all(b["cache_prompt"] is False for b in s.bodies)
    assert len(set(prompts)) == 4
    assert len({p[:20] for p in prompts}) == 4                      # they differ up front
    for i, p in enumerate(prompts):
        assert p.endswith(tokps.QUESTIONS[i % len(tokps.QUESTIONS)])
        assert p == tokps.prompt_for("long", i)                     # reproducible
        assert len(p) > 40000                                       # ~8K tokens of context


def test_a_cache_hit_is_not_a_prefill(stub, tmp_path):
    # Run 2 re-evaluated 1 token (a prompt-cache hit): its "prefill" is dropped.
    s = stub("llama", tps=40.0, prompt_ns=[30, 30, 1, 30])
    rec = tmp_path / "r.json"
    assert tokps.main(["measure", "--url", s.url, "--runs", "3", "--record", str(rec)]) == 0
    d = json.loads(rec.read_text())
    assert d["prefill_tps_median"] == 812.5
    assert d["runs"][2].get("prefill_cached") is True and d["decode_tps_median"] == 40.0


def test_gate_compares_like_with_like(tmp_path):
    # Baseline by server timings, candidate by stream: both stream rates count.
    base = _rec(tmp_path, "llama", 20.0, decode_source="server-timings", stream_tps_median=19.0,
                completion_tokens_median=256)
    cand = _rec(tmp_path, "m", 31.0, decode_source="stream", stream_tps_median=27.0,
                completion_tokens_median=256)
    out = tmp_path / "v.json"
    assert tokps.main(["gate", "--engine", "maya", "--candidate", cand, "--baseline", base,
                       "--out", str(out)]) == 1                       # 27/19 = 1.42x, not 31/20
    v = json.loads(out.read_text())
    assert v["compared_by"] == "stream" and v["ratio"] == 1.421
    # No stream rate on one side: refused, never a cross-method ratio.
    cand2 = _rec(tmp_path, "m2", 31.0, decode_source="stream", completion_tokens_median=256)
    base2 = _rec(tmp_path, "b2", 20.0, decode_source="server-timings", completion_tokens_median=256)
    assert tokps.main(["gate", "--engine", "maya", "--candidate", cand2, "--baseline", base2]) == 1


def test_gate_refuses_a_short_reply_and_an_inflated_self_report(tmp_path):
    base = _rec(tmp_path, "llama", 20.0, decode_source="server-timings", stream_tps_median=19.5,
                completion_tokens_median=256)
    early = _rec(tmp_path, "early", 31.0, decode_source="server-timings", stream_tps_median=30.0,
                 completion_tokens_median=12)
    out = tmp_path / "v.json"
    assert tokps.main(["gate", "--engine", "maya", "--candidate", early, "--baseline", base,
                       "--out", str(out)]) == 1
    assert any("completion lengths differ" in p for p in json.loads(out.read_text())["problems"])
    brag = _rec(tmp_path, "brag", 40.0, decode_source="server-timings", stream_tps_median=25.0,
                completion_tokens_median=256)
    assert tokps.main(["gate", "--engine", "maya", "--candidate", brag, "--baseline", base,
                       "--out", str(out)]) == 1
    assert any("self-reported" in p for p in json.loads(out.read_text())["problems"])
    honest = _rec(tmp_path, "honest", 31.0, decode_source="server-timings", stream_tps_median=30.0,
                  completion_tokens_median=250)
    assert tokps.main(["gate", "--engine", "maya", "--candidate", honest, "--baseline", base]) == 0


def test_gate_records_the_binding(tmp_path):
    base = _rec(tmp_path, "llama", 20.0)
    out = tmp_path / "v.json"
    assert tokps.main(["gate", "--engine", "maya", "--candidate", _rec(tmp_path, "m", 31.0),
                       "--baseline", base, "--bind", "commit=abc", "--bind", "profile=p",
                       "--out", str(out)]) == 0
    assert json.loads(out.read_text())["bound"] == {"commit": "abc", "profile": "p"}
