#!/usr/bin/env python3
"""Tests for the per-task health-check + server-recovery hook in run_eval.

The hook exists because the v3.5 sweep had llama-server die at task 64 of
Gemma's run; the harness silently logged 256 zero-token failures, posting
24% accuracy when the model itself was at 90.6% on what it actually saw.

These tests inject fake `health_check` and `recover_cb` callables and a
fake `run_agent` so we don't need a live llama-server.

Run: python3 tests/test_health_recovery.py
"""

from __future__ import annotations

import importlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))


PASSED = 0
FAILED = 0


def check(label: str, cond: bool, detail: str = ""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [PASS] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label}  {detail}")


def _fresh_run_eval(cache_dir: Path, results_dir: Path):
    """Reload run_eval + cache with isolated dirs so tests don't pollute repo."""
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = cache_dir
    cache._context_cache.clear()
    run_eval = importlib.import_module("run_eval")
    run_eval.RESULTS_DIR = str(results_dir)
    return run_eval, cache


def _make_task_files(tmp_tasks_dir: Path):
    """Two trivial tasks so run_eval has something to iterate over."""
    for tid in ("01_alpha", "02_beta"):
        (tmp_tasks_dir / f"{tid}.json").write_text(json.dumps({
            "id": tid, "name": tid, "difficulty": "easy",
            "task": "noop",
            "setup": "true",
            "validation": {"type": "bash", "script": "true"},
            "cleanup": "true",
            "max_iter": 1,
        }))


def _patch_run_agent(run_eval, fake):
    run_eval.run_agent = fake


def test_healthy_path_runs_normally(td: Path):
    """health_check always True → both tasks run + pass."""
    cache_dir = td / "cache"; cache_dir.mkdir()
    results_dir = td / "results"; results_dir.mkdir()
    tasks_dir = td / "tasks"; tasks_dir.mkdir()
    _make_task_files(tasks_dir)
    run_eval, _ = _fresh_run_eval(cache_dir, results_dir)
    run_eval.TASKS_DIR = str(tasks_dir)

    _patch_run_agent(run_eval, lambda *a, **kw: {
        "exit_code": 0, "elapsed_seconds": 0.1, "stdout": "", "stderr": "",
        "tokens": {"prompt": 100, "completion": 50, "total": 150},
    })

    results = run_eval.run_eval(
        model_name="fake-model",
        use_cache=False,
        health_check=lambda: True,
        recover_cb=lambda: False,  # never invoked
    )
    check("healthy path: 2 tasks attempted",
          len(results["tasks"]) == 2, f"got {len(results['tasks'])}")
    check("healthy path: both pass",
          all(t["passed"] for t in results["tasks"]))
    check("healthy path: no 'server_unhealthy' results",
          not any(t.get("reason") == "server_unhealthy" for t in results["tasks"]))


def test_recovery_succeeds_continues(td: Path):
    """health_check False on task 1, recover_cb True → tasks still complete."""
    cache_dir = td / "cache"; cache_dir.mkdir()
    results_dir = td / "results"; results_dir.mkdir()
    tasks_dir = td / "tasks"; tasks_dir.mkdir()
    _make_task_files(tasks_dir)
    run_eval, _ = _fresh_run_eval(cache_dir, results_dir)
    run_eval.TASKS_DIR = str(tasks_dir)

    _patch_run_agent(run_eval, lambda *a, **kw: {
        "exit_code": 0, "elapsed_seconds": 0.1, "stdout": "", "stderr": "",
        "tokens": {"prompt": 1, "completion": 1, "total": 2},
    })

    health_calls = {"n": 0}
    def health():
        health_calls["n"] += 1
        return health_calls["n"] != 1  # False once, then True

    recover_calls = {"n": 0}
    def recover():
        recover_calls["n"] += 1
        return True

    results = run_eval.run_eval(
        model_name="fake-model",
        use_cache=False,
        health_check=health,
        recover_cb=recover,
    )
    check("recovery: recover_cb invoked exactly once",
          recover_calls["n"] == 1, f"got {recover_calls['n']}")
    check("recovery: both tasks completed",
          len(results["tasks"]) == 2, f"got {len(results['tasks'])}")
    check("recovery: no 'server_unhealthy' results",
          not any(t.get("reason") == "server_unhealthy" for t in results["tasks"]))


def test_recovery_fails_aborts_and_records(td: Path):
    """health_check False, recover_cb False → 1 'server_unhealthy' task, loop breaks.

    This is the v3.5 Gemma scenario: instead of 256 zero-token phantom failures,
    we record one explicit unhealthy entry and stop."""
    cache_dir = td / "cache"; cache_dir.mkdir()
    results_dir = td / "results"; results_dir.mkdir()
    tasks_dir = td / "tasks"; tasks_dir.mkdir()
    _make_task_files(tasks_dir)
    run_eval, cache = _fresh_run_eval(cache_dir, results_dir)
    run_eval.TASKS_DIR = str(tasks_dir)

    agent_calls = {"n": 0}
    def fake_agent(*a, **kw):
        agent_calls["n"] += 1
        return {"exit_code": 0, "elapsed_seconds": 0.1, "stdout": "",
                "stderr": "", "tokens": {"prompt": 1, "completion": 1, "total": 2}}
    _patch_run_agent(run_eval, fake_agent)

    results = run_eval.run_eval(
        model_name="fake-model",
        use_cache=True,  # cache enabled — to verify unhealthy is NOT cached
        health_check=lambda: False,
        recover_cb=lambda: False,
    )
    check("abort: agent never invoked when server is dead",
          agent_calls["n"] == 0, f"got {agent_calls['n']}")
    check("abort: exactly one task recorded (loop broke after first)",
          len(results["tasks"]) == 1, f"got {len(results['tasks'])}")
    check("abort: that task has reason=server_unhealthy",
          results["tasks"][0].get("reason") == "server_unhealthy")
    check("abort: server_unhealthy task has 0 tokens",
          results["tasks"][0].get("tokens_total", 0) == 0)
    # Cache must not contain the unhealthy entry — unlike a real failure,
    # this is environmental and a retry might succeed.
    cached_files = list(cache_dir.glob("*.json"))
    check("abort: server_unhealthy result NOT cached",
          len(cached_files) == 0, f"found {len(cached_files)} cache files")


def main():
    for fn in (test_healthy_path_runs_normally,
               test_recovery_succeeds_continues,
               test_recovery_fails_aborts_and_records):
        with tempfile.TemporaryDirectory() as t:
            print(f"\n{fn.__name__}:")
            try:
                fn(Path(t))
            except Exception as e:
                global FAILED
                FAILED += 1
                print(f"  [CRASH] {fn.__name__}: {e}")
                import traceback; traceback.print_exc()

    print(f"\n{'='*40}")
    print(f"Health-recovery tests: {PASSED} passed, {FAILED} failed")
    print(f"{'='*40}")
    sys.exit(0 if FAILED == 0 else 1)


if __name__ == "__main__":
    main()
