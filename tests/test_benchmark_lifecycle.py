"""benchmark_all's server lifecycle: stop by PID, refuse a foreign port,
don't wait on a dead serve script, don't cool off after no GPU work.

Review findings lifecycle-4 / efficiency-4. Every server here is a fake
serve script in a temp REPO_DIR on a private port; subprocess.run is
recorded so NOTHING in this file can ever issue a real `pkill` — even when
run against the old code as a negative control.
"""

import importlib
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def ba(tmp_path, monkeypatch):
    for mod in ("cache", "run_eval", "benchmark_all"):
        sys.modules.pop(mod, None)
    mod = importlib.import_module("benchmark_all")
    port = _free_port()
    monkeypatch.setattr(mod, "REPO_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setattr(mod, "LLAMA_PORT", port)
    monkeypatch.setattr(mod, "LLAMA_HEALTH_URL", f"http://127.0.0.1:{port}/health")
    calls = []
    real_run = subprocess.run

    def recording_run(cmd, *a, **k):
        calls.append(list(cmd) if isinstance(cmd, (list, tuple)) else [cmd])
        if cmd and cmd[0] in ("pkill", "killall"):
            return subprocess.CompletedProcess(cmd, 1, "", "")   # never for real
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(mod.subprocess, "run", recording_run)
    mod._test_calls = calls
    yield mod
    if getattr(mod, "_own_server", None) and mod._own_server.get("proc"):
        try:
            os.killpg(mod._own_server["proc"].pid, signal.SIGKILL)
        except OSError:
            pass


def _serve(tmp_path: Path, body: str) -> str:
    (tmp_path / "serve.sh").write_text("#!/bin/bash\n" + body + "\n")
    return "serve.sh"


def test_stop_targets_only_our_server(ba, tmp_path):
    decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)",
                              "llama-server"], start_new_session=True)
    proc = None
    try:
        proc, _ = ba.start_model(_serve(tmp_path, "exec sleep 30"), "m")
        ba.stop_llama_server()
        assert proc.poll() is not None, "our server survived stop"
        assert decoy.poll() is None, "a foreign 'llama-server' process was killed"
        assert not any(c and c[0] in ("pkill", "killall") for c in ba._test_calls)
    finally:
        decoy.kill()
        decoy.wait()
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def test_stop_without_own_server_is_a_noop(ba):
    ba.stop_llama_server()
    assert not any(c and c[0] in ("pkill", "killall") for c in ba._test_calls)


def test_refuses_a_port_someone_else_serves(ba, tmp_path):
    held = socket.socket()
    held.bind(("127.0.0.1", ba.LLAMA_PORT))
    held.listen(1)
    try:
        with pytest.raises(ba.PortBusy):
            ba.start_model(_serve(tmp_path, "exec sleep 30"), "m")
        out = ba.benchmark_model({"name": "M", "slug": "m", "serve": "serve.sh"},
                                 None, None)
        assert "already serving" in out["error"] and out["gpu_work"] is False
    finally:
        held.close()


def test_health_wait_gives_up_when_the_serve_script_dies(ba, tmp_path):
    proc, _ = ba.start_model(_serve(tmp_path, "exit 3"), "m")
    t0 = time.time()
    assert ba.wait_for_health(timeout=30, proc=proc) is False
    assert time.time() - t0 < 5


def test_cooloff_only_after_live_gpu_work(ba, monkeypatch):
    slept = []
    monkeypatch.setattr(ba.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ba.scoring, "score_run", lambda r: {
        "capability": 0, "problem_solving": 0, "language_breadth": 0,
        "accuracy": 0, "speed": 0})
    outcomes = iter([
        {"slug": "a", "name": "A", "error": "model failed to become healthy",
         "gpu_work": False},
        {"slug": "b", "name": "B", "results": {"tasks": [{}], "summary": {
            "total": 1, "live_units": 0, "cache_hits": 1}}},
        {"slug": "c", "name": "C", "results": {"tasks": [{}], "summary": {
            "total": 1, "live_units": 1, "cache_hits": 0}}},
        {"slug": "d", "name": "D", "error": "eval crashed: boom"},   # unknown: cool
        {"slug": "e", "name": "E", "results": {"tasks": [{}], "summary": {"total": 1}}},
    ])
    monkeypatch.setattr(ba, "benchmark_model", lambda *a, **k: next(outcomes))
    models = [{"slug": s, "name": s.upper(), "serve": "x"} for s in "abcde"]
    ba.run_sweep(models, None, None, update_leaderboard=False)
    # a: no load; b: full cache replay; c: live; d: crashed mid-eval; e: last.
    assert slept == [ba.COOLOFF_SECONDS, ba.COOLOFF_SECONDS]


def test_low_disk_stops_the_sweep_without_a_cooloff(ba, monkeypatch):
    """A disk-floor abort holds for every model: the sweep stops rather than
    loading each remaining model to abort on its first unit after 600 s."""
    slept, ran = [], []
    monkeypatch.setattr(ba.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ba.scoring, "score_run", lambda r: {
        "capability": 0, "problem_solving": 0, "language_breadth": 0,
        "accuracy": 0, "speed": 0})
    tasks = [{"passed": True, "from_cache": True},
             {"passed": False, "reason": "low_disk"}]
    # run_eval's own count: the low_disk row never ran the agent.
    summary = {"total": 2, "cache_hits": 1, "live_units": 0}

    def fake(model, *a, **k):
        ran.append(model["slug"])
        return {"slug": model["slug"], "name": model["name"],
                "results": {"tasks": list(tasks), "summary": dict(summary)}}

    monkeypatch.setattr(ba, "benchmark_model", fake)
    models = [{"slug": s, "name": s.upper(), "serve": "x"} for s in "abc"]
    out = ba.run_sweep(models, None, None, update_leaderboard=False)
    assert ran == ["a"] and slept == []
    assert [s["slug"] for s in out["skipped"]] == ["b", "c"]


def test_live_units_exclude_rows_that_never_ran_the_agent(tmp_path, monkeypatch):
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    import json as _json
    import shutil
    import collections
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = tmp_path / "cache"
    run_eval = importlib.import_module("run_eval")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "01_a.json").write_text(_json.dumps({
        "id": "01_a", "name": "a", "difficulty": "easy", "task": "a",
        "validation": {"type": "bash", "script": "true"}, "max_iter": 3}))
    run_eval.TASKS_DIR = str(tasks)
    run_eval.RESULTS_DIR = str(tmp_path / "results")
    U = collections.namedtuple("U", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: U(10**12, 10**12 - 10**9, 10**9))
    monkeypatch.setenv("OPENBEAST_EVAL_MIN_FREE_GB", "5")
    for name in ("capture_server_config", "capture_gpu_info", "capture_inference_engine_info"):
        monkeypatch.setattr(run_eval, name, lambda: {})
    res = run_eval.run_eval(model_name="m")
    assert res["tasks"][0]["reason"] == "low_disk"
    assert res["summary"]["live_units"] == 0
