"""Review 2026-10-27 eval-harness fixes (R6-3, R6-6, R6-8, R6-9, R6-10, R9-04).

Every case builds its own input: no GPU, no real server, no real fixture
dir, no real lease. Each asserts the negative control too.
"""

import importlib
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
sys.path.insert(0, str(EVALS))
sys.path.insert(0, str(ROOT / "agents"))


def _fresh(mod):
    for m in ("cache", "run_eval", "benchmark_all", "scoring", "profile_mtp"):
        sys.modules.pop(m, None)
    return importlib.import_module(mod)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie is dead for our purposes.
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


# ---------------------------------------------------------------------------
# R6-3: benchmark_all's SIGTERM handler must reap the live agents.
# ---------------------------------------------------------------------------

def test_benchmark_all_sigterm_reaps_live_agents(tmp_path):
    """Drive the real benchmark_all.main() (cache-only: no lease, no server)
    with run_sweep replaced by a fake that starts an 'agent' in its own
    session, registers it the way run_eval does, and blocks. SIGTERM the
    driver: the agent must die with it."""
    marker = tmp_path / "agent.pid"
    driver = tmp_path / "driver.py"
    driver.write_text(textwrap.dedent(f"""
        import os, subprocess, sys, time
        sys.path.insert(0, {str(EVALS)!r})
        sys.path.insert(0, {str(ROOT / 'agents')!r})
        import benchmark_all, run_eval

        def fake_sweep(*a, **k):
            child = subprocess.Popen(
                [sys.executable, "-c",
                 "import os, time; open({str(marker)!r}, 'w').write(str(os.getpid())); time.sleep(120)"],
                start_new_session=True)
            with run_eval._live_pgid_lock:
                run_eval._live_pgids.add(child.pid)
            time.sleep(120)
            return {{}}

        benchmark_all.run_sweep = fake_sweep
        slug = benchmark_all.MODELS[0]["slug"]
        sys.argv = ["benchmark_all.py", "--cache-only", "--no-leaderboard", "--models", slug]
        benchmark_all.main()
    """))
    p = subprocess.Popen([sys.executable, str(driver)], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    agent = None
    try:
        for _ in range(200):
            if marker.exists() and marker.read_text().strip():
                agent = int(marker.read_text())
                break
            time.sleep(0.05)
        assert agent, "fake agent never started"
        assert _alive(agent)                         # negative control
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=20)
        assert p.returncode == 130, p.stdout.read()
        for _ in range(100):
            if not _alive(agent):
                break
            time.sleep(0.05)
        assert not _alive(agent), "agent orphaned by benchmark_all's SIGTERM handler"
    finally:
        if p.poll() is None:
            p.kill()
        if agent and _alive(agent):
            os.kill(agent, signal.SIGKILL)


# ---------------------------------------------------------------------------
# R6-6: the Sandlock wrapper is stamped and keyed — only when set.
# ---------------------------------------------------------------------------

def _fresh_eval(tmp_path: Path, monkeypatch):
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = tmp_path / "cache"
    cache.STRIKES_DIR = cache.CACHE_DIR / "env-strikes"
    cache._context_cache.clear()
    run_eval = importlib.import_module("run_eval")
    monkeypatch.setattr(run_eval, "_TOOLCHAINS", {"zig": "0.16.0"})
    monkeypatch.setattr(run_eval, "WEIGHTS_REGISTRY", str(tmp_path / "weights.registry"))
    for var in ("OPENBEAST_EVAL_ENV_ERA", "OPENBEAST_BASH_WRAPPER", "OPENBEAST_EVAL_GREEDY",
                "BEAST_ASSIST", "OPENBEAST_DIAGNOSTICS", "BEAST_PACKS", "OPENBEAST_PACKS",
                "BEAST_ESCALATE", "OPENBEAST_ESCALATE"):
        monkeypatch.delenv(var, raising=False)
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "01_alpha.json").write_text(json.dumps({
        "id": "01_alpha", "name": "alpha", "difficulty": "easy", "task": "do alpha",
        "validation": {"type": "bash", "script": "true"}, "max_iter": 2}))
    monkeypatch.setattr(run_eval, "TASKS_DIR", str(tasks))
    monkeypatch.setattr(run_eval, "RESULTS_DIR", str(tmp_path / "results"))
    gguf = tmp_path / "Q.gguf"
    gguf.write_bytes(b"x")
    monkeypatch.setattr(run_eval, "capture_server_config",
                        lambda *a, **k: run_eval._parse_server_flags(f"llama-server -m {gguf}"))
    monkeypatch.setattr(run_eval, "capture_gpu_info", lambda: {})
    monkeypatch.setattr(run_eval, "capture_inference_engine_info",
                        lambda: {"build": "b1", "commit": "abc"})
    seen_env = []

    def fake_agent(task, *a, **k):
        seen_env.append(os.environ.get("OPENBEAST_BASH_WRAPPER"))
        return {"exit_code": 0, "elapsed_seconds": 1.0, "stdout": "", "stderr": "",
                "tokens": {"prompt": 1, "completion": 2, "total": 3}, "iterations": 1,
                "compactions": 0, "api_errors": 0}
    monkeypatch.setattr(run_eval, "run_agent", fake_agent)
    monkeypatch.setattr(run_eval, "run_validation", lambda t, **k: (True, "ok"))
    return run_eval, cache, seen_env


def test_sandbox_wrapper_unset_keeps_existing_key(tmp_path, monkeypatch):
    run_eval, cache, _ = _fresh_eval(tmp_path, monkeypatch)
    res = run_eval.run_eval(model_name="m")
    keys = [p.name for p in cache.CACHE_DIR.glob("*.json")]
    task = run_eval.load_tasks(None)[0]
    legacy = cache.cache_key(task, "m", max_iter=2) + ".json"
    assert keys == [legacy]                       # byte-identical legacy key
    assert "sbx-" not in legacy
    assert "bash_wrapper" not in res["harness"]
    # Blank counts as unset (tools.py strips it too).
    monkeypatch.setenv("OPENBEAST_BASH_WRAPPER", "   ")
    res = run_eval.run_eval(model_name="m")
    assert res["tasks"][0].get("from_cache")
    assert "bash_wrapper" not in res["harness"]


def test_sandbox_wrapper_set_forks_key_and_stamps(tmp_path, monkeypatch):
    run_eval, cache, seen = _fresh_eval(tmp_path, monkeypatch)
    run_eval.run_eval(model_name="m")             # banks the unconfined row
    monkeypatch.setenv("OPENBEAST_BASH_WRAPPER", " sandlock --profile openbeast -- ")
    res = run_eval.run_eval(model_name="m")
    assert not res["tasks"][0].get("from_cache"), "confined unit replayed an unconfined row"
    h = res["harness"]
    assert h["bash_wrapper"] == "sandlock --profile openbeast --"
    assert h["bash_wrapper_component"].startswith("sbx-")
    keys = sorted(p.name for p in cache.CACHE_DIR.glob("*.json"))
    assert len(keys) == 2 and sum(f".{h['bash_wrapper_component']}." in k for k in keys) == 1
    assert seen[-1] == "sandlock --profile openbeast --"
    # A different wrapper is a different era again.
    monkeypatch.setenv("OPENBEAST_BASH_WRAPPER", "other-sandbox --")
    assert not run_eval.run_eval(model_name="m")["tasks"][0].get("from_cache")


def test_sandbox_wrapper_is_an_experiment_arm():
    ba = _fresh("benchmark_all")
    assert ba.experiment_arms({}) == []
    assert ba.experiment_arms({"OPENBEAST_BASH_WRAPPER": "  "}) == []
    assert ba.experiment_arms({"OPENBEAST_BASH_WRAPPER": "sandlock --"}) == [
        "OPENBEAST_BASH_WRAPPER sandbox"]
