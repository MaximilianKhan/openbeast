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
