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
