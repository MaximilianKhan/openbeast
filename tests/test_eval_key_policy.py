"""run_eval's server probes obey the runner's ambient-key policy.

detect_model() and _fetch_server_slots() attached OPENBEAST_API_KEY /
OPENAI_API_KEY to whatever --base-url they were given, while the runner
child (resolve_api_key / _key_endpoint_trusted) withholds the env key from
any host that is neither loopback nor OPENBEAST_AGENT_INFERENCE_URL
(review finding campaign-evals: harness leaks the ambient key). No network.
"""

import importlib
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))


@pytest.fixture
def probe(monkeypatch):
    pytest.importorskip("openai")
    run_eval = importlib.import_module("run_eval")
    seen = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen.append((req.full_url, req.get_header("Authorization")))
        return _Resp(json.dumps({"data": [{"id": "m"}], "total_slots": 2}).encode())

    monkeypatch.setattr(run_eval.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("OPENBEAST_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.delenv("OPENBEAST_AGENT_INFERENCE_URL", raising=False)
    return run_eval, seen


def test_foreign_host_gets_no_ambient_key(probe):
    run_eval, seen = probe
    assert run_eval.detect_model("http://elsewhere.example:8080/v1") == "m"
    assert run_eval._fetch_server_slots("http://elsewhere.example:8080/v1") == 2
    assert [h for _, h in seen] == [None, None], seen


def test_loopback_and_configured_host_keep_the_key(probe, monkeypatch):
    run_eval, seen = probe
    run_eval.detect_model("http://127.0.0.1:8080/v1")
    monkeypatch.setenv("OPENBEAST_AGENT_INFERENCE_URL", "http://rig.tailnet:8443/v1")
    run_eval._fetch_server_slots("http://rig.tailnet:8443/v1")
    assert [h for _, h in seen] == ["Bearer sk-secret", "Bearer sk-secret"], seen


def test_every_probe_withholds_the_key_from_a_foreign_host(probe, monkeypatch):
    """The /v1/models fallback of _fetch_server_slots (taken when /props is
    absent: vLLM, TensorFold, a gated host) and capture_remote_server_info
    read the env key directly and sent it to any --base-url (review 1116)."""
    run_eval, seen = probe
    monkeypatch.setenv("OPENBEAST_API_KEY", "DUMMY-CANARY")
    real = run_eval.urllib.request.urlopen

    def no_props(req, timeout=None):
        if req.full_url.endswith("/props"):
            seen.append((req.full_url, req.get_header("Authorization")))
            raise OSError("404")
        return real(req, timeout=timeout)

    monkeypatch.setattr(run_eval.urllib.request, "urlopen", no_props)
    base = "http://other-host.example:8000/v1"
    run_eval.detect_model(base)
    run_eval._fetch_server_slots(base)
    info = run_eval.capture_remote_server_info(base)
    assert info.get("model_id") == "m"
    assert [u.rsplit("/", 1)[1] for u, _ in seen] == ["models", "props", "models", "models"]
    assert [h for _, h in seen] == [None] * 4, seen


def test_every_probe_keeps_the_key_for_the_configured_host(probe, monkeypatch):
    run_eval, seen = probe
    base = "https://spark.tailnet:8443/v1"
    monkeypatch.setenv("OPENBEAST_AGENT_INFERENCE_URL", base)
    run_eval.capture_remote_server_info(base)
    assert [h for _, h in seen] == ["Bearer sk-secret"], seen


def test_no_probe_reads_the_ambient_key_itself():
    """Static pin: only _auth_headers (through runner.resolve_api_key) may
    turn the ambient key into a header."""
    src = (ROOT / "evals" / "run_eval.py").read_text()
    assert 'os.environ.get("OPENBEAST_API_KEY")' not in src
    assert 'os.environ.get("OPENAI_API_KEY")' not in src



class _GateResp(io.BytesIO):
    def __init__(self, data, headers):
        super().__init__(data)
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _gate(monkeypatch, run_eval, cap, gate=True):
    import urllib.error
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.get_method(), req.full_url, req.data))
        if req.full_url.endswith("/models"):
            hdr = {"X-OpenBeast-Request-Id": "r1"} if gate else {}
            return _GateResp(json.dumps({"data": [{"id": "GLM"}]}).encode(), hdr)
        body = json.loads(req.data)
        assert body["n"] > cap and body["max_tokens"] == 1
        msg = (f"request fans out into {body['n']} generations (prompts x n); this device "
               f"may run at most {cap} at once — split it into smaller requests")
        raise urllib.error.HTTPError(req.full_url, 400, "x", {},
                                     io.BytesIO(json.dumps({"error": {"message": msg}}).encode()))
    monkeypatch.setattr(run_eval.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_gate_inflight_cap_is_read_without_generating(probe, monkeypatch):
    """beast-gate admits EDGE_MAX_INFLIGHT generations per device and every
    agent shares one key: --jobs 4 against spark-1's default 2 got 429 on
    half its generations (review 1116). The harness reads the cap from the
    gate's fan-out refusal, which forwards nothing."""
    run_eval, _ = probe
    calls = _gate(monkeypatch, run_eval, cap=2)
    assert run_eval._fetch_gate_inflight_cap("https://spark.example:8443/v1") == 2
    assert [c[0] for c in calls] == ["GET", "POST"]


def test_a_non_gate_endpoint_never_gets_the_probe_post(probe, monkeypatch):
    run_eval, _ = probe
    calls = _gate(monkeypatch, run_eval, cap=2, gate=False)
    assert run_eval._fetch_gate_inflight_cap("http://vllm.example:8000/v1") is None
    assert [c[0] for c in calls] == ["GET"]



@pytest.mark.parametrize("cap", [2, 5])
def test_gate_cap_probe_against_the_real_gate(probe, monkeypatch, tmp_path, cap):
    """The same probe against agents/edge.py itself (stub upstream, real
    admission code): it reads EDGE_MAX_INFLIGHT and nothing is forwarded."""
    pytest.importorskip("starlette")
    pytest.importorskip("httpx")
    import importlib as _il
    import urllib.error

    from starlette.testclient import TestClient
    sys.path.insert(0, str(ROOT / "tests"))
    import test_edge as TE
    run_eval, _ = probe
    monkeypatch.setenv("OPENBEAST_REPO_DIR", str(tmp_path))
    monkeypatch.delenv("OPENBEAST_EDGE_ALLOW_ANON", raising=False)
    monkeypatch.setenv("OPENBEAST_EDGE_MAX_INFLIGHT", str(cap))
    import edge as _edge
    _il.reload(_edge)
    TE._registry(tmp_path)
    captured = {}
    TE._stub_upstream(_edge, captured)
    base = "https://spark.example:8443/v1"
    monkeypatch.setenv("OPENBEAST_AGENT_INFERENCE_URL", base)
    monkeypatch.setenv("OPENBEAST_API_KEY", TE.DEVICE_KEY)
    with TestClient(_edge.app) as c:
        def via_gate(req, timeout=None):
            path = req.full_url[len("https://spark.example:8443"):]
            r = c.request(req.get_method(), path, content=req.data,
                          headers=dict(req.header_items()))
            if r.status_code >= 400:
                raise urllib.error.HTTPError(req.full_url, r.status_code, "x", r.headers,
                                             io.BytesIO(r.content))
            return _GateResp(r.content, r.headers)
        monkeypatch.setattr(run_eval.urllib.request, "urlopen", via_gate)
        assert run_eval._fetch_gate_inflight_cap(base) == cap
    assert captured.get("method") != "POST", "the probe POST reached the upstream"
