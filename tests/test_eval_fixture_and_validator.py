"""Two ways a unit's verdict was not the model's (review 2026-10-09, evals
F7 / F8), and the wall-timeout token read (F12).

  * F7 — every task's setup is `mkdir -p`: a solution left in the fixture
    dir by a killed run validated for the NEXT unit to use that dir, even
    when its agent wrote nothing.
  * F8 — a validator timeout (fixed 30 s, not scaled under --jobs) was
    banked as a deterministic model FAIL.
  * F12 — a wall-timeout row recorded tokens 0 whatever the runner had
    already reported.

Every case builds its own task in a temp dir with a fake agent / fake
runner / fake validator. No server, no GPU, no real /tmp/eval_* fixture.
"""

import importlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))

_AGENT = {"exit_code": 0, "elapsed_seconds": 1.0, "stdout": "", "stderr": "",
          "tokens": {"prompt": 10, "completion": 500, "total": 510},
          "iterations": 3, "compactions": 0, "api_errors": 0}


def _fresh(tmp_path: Path, task: dict | None = None):
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = tmp_path / "cache"
    cache.STRIKES_DIR = cache.CACHE_DIR / "env-strikes"
    cache._context_cache.clear()
    run_eval = importlib.import_module("run_eval")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "01_alpha.json").write_text(json.dumps(task or {
        "id": "01_alpha", "name": "alpha", "difficulty": "easy",
        "task": "do alpha", "validation": {"type": "bash", "script": "false"},
        "max_iter": 3}))
    run_eval.TASKS_DIR = str(tasks)
    run_eval.RESULTS_DIR = str(tmp_path / "results")
    return run_eval, cache


def _quiet(run_eval, monkeypatch):
    monkeypatch.setattr(run_eval, "capture_server_config", lambda *a, **k: {})
    monkeypatch.setattr(run_eval, "capture_gpu_info", lambda: {})
    monkeypatch.setattr(run_eval, "capture_inference_engine_info", lambda: {})


# --- F7: a leftover solution must not validate for the next unit -----------

def _fixture_task(tmp_path: Path) -> tuple[dict, Path]:
    fx = tmp_path / "fx"
    return {
        "id": "01_alpha", "name": "alpha", "difficulty": "easy", "max_iter": 3,
        "setup": f"mkdir -p {fx}",
        "task": "write the solution",
        "validation": {"type": "bash", "script": f"test -f {fx}/solution.py"},
        "cleanup": f"rm -rf {fx}"}, fx


def test_leftover_solution_from_a_killed_run_does_not_pass(tmp_path, monkeypatch):
    task, fx = _fixture_task(tmp_path)
    run_eval, cache = _fresh(tmp_path, task)
    _quiet(run_eval, monkeypatch)
    fx.mkdir()
    (fx / "solution.py").write_text("# model A's answer, left by a killed run\n")
    # Model B's agent writes nothing at all.
    monkeypatch.setattr(run_eval, "run_agent", lambda *a, **k: dict(_AGENT))
    res = run_eval.run_eval(model_name="model-b")
    assert res["tasks"][0]["passed"] is False
    (banked,) = cache.CACHE_DIR.glob("*.json")
    assert json.loads(banked.read_text())["passed"] is False


def test_a_solution_the_agent_writes_still_passes(tmp_path, monkeypatch):
    """Negative control: the pre-setup cleanup runs BEFORE the agent, so it
    never removes this unit's own work."""
    task, fx = _fixture_task(tmp_path)
    run_eval, _ = _fresh(tmp_path, task)
    _quiet(run_eval, monkeypatch)
    fx.mkdir()
    (fx / "solution.py").write_text("# stale\n")

    def agent(*a, **k):
        assert not (fx / "solution.py").exists(), "stale fixture survived into the agent run"
        assert fx.is_dir(), "setup did not recreate the fixture dir"
        (fx / "solution.py").write_text("# fresh\n")
        return dict(_AGENT)

    monkeypatch.setattr(run_eval, "run_agent", agent)
    res = run_eval.run_eval(model_name="model-b")
    assert res["tasks"][0]["passed"] is True


# --- F8: a validator timeout is not a model verdict ------------------------

def _raise_timeout(*a, **k):
    raise subprocess.TimeoutExpired("validator", k.get("timeout", 0))


def test_validator_timeout_budget_scales_with_contention(tmp_path, monkeypatch):
    run_eval, _ = _fresh(tmp_path)
    seen = []
    monkeypatch.setattr(run_eval, "_run_reaped",
                        lambda cmd, timeout, shell=False: seen.append(timeout) or (0, "ok"))
    task = {"validation": {"type": "bash", "script": "x"}}
    run_eval.run_validation(task)
    run_eval.run_validation(task, timeout_scale=2.0)
    run_eval.run_validation({"validation": {"type": "python", "script": "x"}}, timeout_scale=2.0)
    assert seen == [30, 60, 60]


def test_run_eval_hands_the_jobs_scale_to_the_validator(tmp_path, monkeypatch):
    run_eval, _ = _fresh(tmp_path)
    _quiet(run_eval, monkeypatch)
    monkeypatch.setattr(run_eval, "_fetch_server_slots", lambda base_url: 8)
    monkeypatch.setattr(run_eval, "run_agent", lambda *a, **k: dict(_AGENT))
    scales = []
    monkeypatch.setattr(run_eval, "run_validation",
                        lambda t, timeout_scale=1.0: scales.append(timeout_scale) or (True, "ok"))
    run_eval.run_eval(model_name="m", use_cache=False, jobs=4)
    run_eval.run_eval(model_name="m", use_cache=False, jobs=1)
    assert scales == [2.0, 1.0]


def test_validator_timeout_has_its_own_reason_and_is_not_cached(tmp_path, monkeypatch):
    run_eval, cache = _fresh(tmp_path)
    _quiet(run_eval, monkeypatch)
    monkeypatch.setattr(run_eval, "run_agent", lambda *a, **k: dict(_AGENT))
    monkeypatch.setattr(run_eval, "_run_reaped", _raise_timeout)
    res = run_eval.run_eval(model_name="m")
    row = res["tasks"][0]
    assert row["passed"] is False and row["reason"] == "validator_timeout"
    assert run_eval.validator_timed_out(row["validation_output"])
    assert "30s" in row["validation_output"]
    assert not list(cache.CACHE_DIR.glob("*.json"))


def test_cacheable_result_refuses_a_validator_timeout(tmp_path):
    run_eval, _ = _fresh(tmp_path)
    base = {"passed": False, "agent_exit_code": 0, "tokens_completion": 5123}
    # The pre-fix message, and the new one that names the budget.
    for text in ("Validation timed out", "Validation timed out (60s)"):
        assert run_eval.cacheable_result({**base, "validation_output": text}) is False, text
    assert run_eval.cacheable_result({**base, "reason": "validator_timeout",
                                      "validation_output": "x"}) is False
    # Negative controls: an ordinary FAIL banks; so does a model program
    # that merely PRINTS about a timeout; so does a repeat offender.
    assert run_eval.cacheable_result({**base, "validation_output": "expected 3 got 4"}) is True
    assert run_eval.cacheable_result(
        {**base, "validation_output": "AssertionError: request timed out"}) is True
    assert run_eval.cacheable_result({**base, "validation_output": "Validation timed out (30s)",
                                      "validator_timeout_repeats": 3}) is True


def test_a_solution_that_always_hangs_banks_as_a_fail(tmp_path, monkeypatch):
    """Never cached, a hanging solution would rerun its agent on every
    relaunch. The same key timing out N runs running is the model's own."""
    run_eval, cache = _fresh(tmp_path)
    _quiet(run_eval, monkeypatch)
    monkeypatch.delenv("OPENBEAST_EVAL_ENV_ERROR_BANK_AFTER", raising=False)
    monkeypatch.setattr(run_eval, "run_agent", lambda *a, **k: dict(_AGENT))
    monkeypatch.setattr(run_eval, "_run_reaped", _raise_timeout)
    for _ in range(2):
        res = run_eval.run_eval(model_name="m")
        assert res["tasks"][0]["reason"] == "validator_timeout"
        assert not list(cache.CACHE_DIR.glob("*.json"))
    row = run_eval.run_eval(model_name="m")["tasks"][0]
    assert "reason" not in row and row["validator_timeout_repeats"] == 3
    assert len(list(cache.CACHE_DIR.glob("*.json"))) == 1
    assert not list(cache.STRIKES_DIR.glob("*.json"))
