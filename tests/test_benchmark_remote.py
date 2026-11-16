"""benchmark_all's remote MODELS entries (v5): GLM-5.3-Flash on the
spark-hydra pair is reached at a --base-url this harness never starts or
stops. Its key comes from the env (never the file), --jobs is clamped to
its slots, its reasoning budget is declared, and it never takes the local
GPU lease."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))

import benchmark_all as B  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_remote_env(monkeypatch):
    # benchmark_remote exports the endpoint and key into os.environ (the
    # sweep process is the runner's parent). Registered here, monkeypatch puts
    # them back after the test: leaked, OPENBEAST_AGENT_INFERENCE_URL made a
    # later chat_server test's runner argv carry --base-url, and the key
    # changed test_eval_key_policy's ambient-key verdicts.
    # (setenv first: delenv of an ABSENT var records nothing to undo.)
    for k in ("OPENBEAST_AGENT_INFERENCE_URL", "OPENBEAST_API_KEY"):
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)


def _glm():
    return next(m for m in B.MODELS if m["slug"] == "glm-5.3-flash")


def test_glm_entry_is_remote_with_slots_and_a_key_env():
    m = _glm()
    assert B.is_remote(m) and "serve" not in m
    assert m["base_url"] == "https://spark-1.tail4109f9.ts.net:8443/v1"
    assert m["slots"] == 4 and m["api_key_env"] and m["reasoning_budget"] == "0"
    assert not any(B.is_remote(x) for x in B.MODELS if x["slug"] != "glm-5.3-flash")


def test_remote_env_needs_the_key_and_marks_the_endpoint():
    m = _glm()
    with pytest.raises(KeyError):
        B.remote_env(m, env={})
    env = B.remote_env(m, env={m["api_key_env"]: "k-123"})
    assert env == {"OPENBEAST_API_KEY": "k-123",
                   "OPENBEAST_AGENT_INFERENCE_URL": m["base_url"]}


def test_benchmark_remote_passes_slots_url_and_declared_budget(monkeypatch):
    m = _glm()
    monkeypatch.setenv(m["api_key_env"], "k-xyz")
    seen = {}
    monkeypatch.setattr(B.run_eval, "detect_model", lambda url: "GLM-5.3-Flash-EXL3")
    monkeypatch.setattr(B.run_eval, "run_eval", lambda **kw: seen.update(kw) or
                        {"tasks": [], "summary": {"total": 0}})
    # never touches the local server
    monkeypatch.setattr(B, "stop_llama_server",
                        lambda: (_ for _ in ()).throw(AssertionError("stopped a local server")))
    monkeypatch.setattr(B, "start_model",
                        lambda *a: (_ for _ in ()).throw(AssertionError("started a model")))
    out = B.benchmark_model(m, None, None, jobs=8, suite="v5")
    assert "results" in out
    assert seen["base_url"] == m["base_url"] and seen["slots"] == 4 and seen["jobs"] == 8
    assert seen["reasoning_budget"] == "0" and seen["recover_cb"] is None
    import os
    assert os.environ["OPENBEAST_API_KEY"] == "k-xyz"


def test_benchmark_remote_refuses_without_a_key(monkeypatch):
    m = _glm()
    monkeypatch.delenv(m["api_key_env"], raising=False)
    out = B.benchmark_model(m, None, None)
    assert "error" in out and m["api_key_env"] in out["error"] and out["gpu_work"] is False
