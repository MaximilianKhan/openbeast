"""The eval environment fingerprint (review finding eval-harness-7).

The cache key never saw the GGUF bytes, the llama.cpp build, the KV/context
serve flags or the validator toolchains, so a quant regenerated under the
same alias replayed the OLD quant's verdicts. Now every live row carries an
`env1-<sha8>` fingerprint, replays from another environment are counted,
and OPENBEAST_EVAL_ENV_ERA=1 puts the fingerprint into the key. Everything
here is stubbed: fake weights in tmp, a fake registry, fixed toolchains.
"""

import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))


def _fresh(tmp_path: Path, monkeypatch):
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = tmp_path / "cache"
    cache.STRIKES_DIR = cache.CACHE_DIR / "env-strikes"
    cache._context_cache.clear()
    run_eval = importlib.import_module("run_eval")
    monkeypatch.setattr(run_eval, "_TOOLCHAINS", {"zig": "0.16.0"})
    monkeypatch.setattr(run_eval, "WEIGHTS_REGISTRY", str(tmp_path / "weights.registry"))
    monkeypatch.delenv("OPENBEAST_EVAL_ENV_ERA", raising=False)
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "01_alpha.json").write_text(json.dumps({
        "id": "01_alpha", "name": "alpha", "difficulty": "easy", "task": "do alpha",
        "validation": {"type": "bash", "script": "true"}, "max_iter": 2}))
    run_eval.TASKS_DIR = str(tasks)
    run_eval.RESULTS_DIR = str(tmp_path / "results")
    return run_eval, cache


def test_legacy_key_shape_unchanged(tmp_path, monkeypatch):
    _, cache = _fresh(tmp_path, monkeypatch)
    t = {"id": "x", "task": "y"}
    assert cache.cache_key(t, "m", max_iter=3) == cache.cache_key(t, "m", max_iter=3, env=None)
    assert ".env1-abcd1234." in cache.cache_key(t, "m", max_iter=3, env="env1-abcd1234")


def test_weight_identity_pinned_vs_regenerated(tmp_path, monkeypatch):
    run_eval, _ = _fresh(tmp_path, monkeypatch)
    gguf = tmp_path / "Q.gguf"
    gguf.write_bytes(b"x" * 100)
    (tmp_path / "weights.registry").write_text(
        "# comment\nfeedface\t100\tQ.gguf\trepo\tremote\n")
    pinned = run_eval.weight_identity(str(gguf))
    assert pinned == {"file": "Q.gguf", "bytes": 100, "sha256": "feedface"}
    # Re-emitted with a different size: not the pinned file any more.
    gguf.write_bytes(b"y" * 101)
    regen = run_eval.weight_identity(str(gguf))
    assert "sha256" not in regen and regen["bytes"] == 101 and "mtime_ns" in regen
    # Same size, new mtime (a research quant rewritten in place): differs too.
    os.utime(gguf, ns=(1, 1))
    assert run_eval.weight_identity(str(gguf)) != regen


def test_fingerprint_moves_with_each_component(tmp_path, monkeypatch):
    run_eval, _ = _fresh(tmp_path, monkeypatch)
    gguf = tmp_path / "Q.gguf"
    gguf.write_bytes(b"x")
    server = run_eval._parse_server_flags(f"llama-server -m {gguf} -c 8192 -ctk q8_0 -ctv q8_0")
    assert server["model_path"] == str(gguf) and server["kv_cache_type_v"] == "q8_0"
    engine = {"build": "b1", "commit": "abc"}
    base, _ = run_eval.env_fingerprint(server, engine)
    assert base.startswith("env1-")
    assert run_eval.env_fingerprint(server, engine)[0] == base          # deterministic
    assert run_eval.env_fingerprint(server, {"build": "b2", "commit": "def"})[0] != base
    assert run_eval.env_fingerprint({**server, "kv_cache_type": "q4_0"}, engine)[0] != base
    monkeypatch.setattr(run_eval, "_TOOLCHAINS", {"zig": "0.16.1"})
    assert run_eval.env_fingerprint(server, engine)[0] != base


def _live(run_eval, monkeypatch, server_cmd: str):
    monkeypatch.setattr(run_eval, "capture_server_config",
                        lambda *a, **k: run_eval._parse_server_flags(server_cmd))
    monkeypatch.setattr(run_eval, "capture_gpu_info", lambda: {})
    monkeypatch.setattr(run_eval, "capture_inference_engine_info",
                        lambda: {"build": "b1", "commit": "abc"})
    monkeypatch.setattr(run_eval, "run_agent", lambda *a, **k: {
        "exit_code": 0, "elapsed_seconds": 1.0, "stdout": "", "stderr": "",
        "tokens": {"prompt": 1, "completion": 2, "total": 3}, "iterations": 1,
        "compactions": 0, "api_errors": 0})
    monkeypatch.setattr(run_eval, "run_validation", lambda t, **k: (True, "ok"))
    return run_eval.run_eval(model_name="m")


def test_replay_from_another_environment_is_counted(tmp_path, monkeypatch, capsys):
    run_eval, cache = _fresh(tmp_path, monkeypatch)
    gguf = tmp_path / "Q.gguf"
    gguf.write_bytes(b"x")
    first = _live(run_eval, monkeypatch, f"llama-server -m {gguf}")
    fp = first["harness"]["env_component"]
    assert first["tasks"][0]["env_fp"] == fp
    assert first["harness"]["env_in_cache_key"] is False
    # Same environment: a clean replay, no drift reported.
    again = _live(run_eval, monkeypatch, f"llama-server -m {gguf}")
    assert again["tasks"][0]["from_cache"] and "env_drift_replays" not in again["summary"]
    # The quant is regenerated under the same alias: the legacy key still
    # replays the old verdict, but it is now counted and disclosed.
    gguf.write_bytes(b"xx")
    capsys.readouterr()
    drift = _live(run_eval, monkeypatch, f"llama-server -m {gguf}")
    assert drift["tasks"][0]["from_cache"]
    assert drift["summary"]["env_drift_replays"] == 1
    assert "ENV DRIFT" in capsys.readouterr().out


def test_env_era_opt_in_forks_the_key(tmp_path, monkeypatch):
    run_eval, cache = _fresh(tmp_path, monkeypatch)
    gguf = tmp_path / "Q.gguf"
    gguf.write_bytes(b"x")
    _live(run_eval, monkeypatch, f"llama-server -m {gguf}")
    monkeypatch.setenv("OPENBEAST_EVAL_ENV_ERA", "1")
    res = _live(run_eval, monkeypatch, f"llama-server -m {gguf}")
    assert not res["tasks"][0].get("from_cache")               # own era: ran live
    assert res["harness"]["env_in_cache_key"] is True
    keys = sorted(p.name for p in cache.CACHE_DIR.glob("*.json"))
    assert len(keys) == 2 and sum(".env1-" in k for k in keys) == 1
    gguf.write_bytes(b"xx")                                     # regenerated quant
    res = _live(run_eval, monkeypatch, f"llama-server -m {gguf}")
    assert not res["tasks"][0].get("from_cache")
