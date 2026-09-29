"""Infrastructure failures must never be banked as model FAILs.

Two classes, both seen in the 2026-09 campaigns (docs/reviews/FULL-REVIEW-
2026-09-29.md, eval-harness-1 / eval-harness-6 / storage-02):

  * server_error — llama-server died AFTER the model had spoken. The runner
    burned its remaining iterations on "Connection error." and exited 0 with
    tokens > 0, so cacheable_result banked a permanent FAIL that the Tier-3
    verdict then replayed.
  * env_error — the VALIDATOR died to fork/thread EAGAIN (RLIMIT_NPROC is
    uid-global) or a full disk; exit 0, tokens > 0, banked as a FAIL.

Every case is built here: a fake OpenAI client for the runner, a fake
run_agent / health check / validation for run_eval. No server, no GPU.
"""

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))


def _fresh(tmp_path: Path):
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = tmp_path / "cache"
    cache._context_cache.clear()
    run_eval = importlib.import_module("run_eval")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "01_alpha.json").write_text(json.dumps({
        "id": "01_alpha", "name": "alpha", "difficulty": "easy",
        "task": "do alpha", "validation": {"type": "bash", "script": "false"},
        "max_iter": 3}))
    run_eval.TASKS_DIR = str(tasks)
    run_eval.RESULTS_DIR = str(tmp_path / "results")
    return run_eval, cache


# --- runner: the stable API_ERRORS line ------------------------------------

class _DyingClient:
    """Answers the first call with a plain message, then the 'server' is gone."""

    def __init__(self):
        self.chat = self
        self.completions = self
        self.n = 0

    def create(self, model, messages, tools, temperature, **kw):
        self.n += 1
        if self.n == 1:
            msg = type("M", (), {"content": "thinking", "tool_calls": []})()
            usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 50,
                                   "total_tokens": 60})()
            return type("R", (), {
                "choices": [type("C", (), {"message": msg, "finish_reason": "length"})()],
                "usage": usage})()
        raise ConnectionError("Connection error.")


def test_runner_counts_api_errors(tmp_path, monkeypatch, capsys):
    import runner
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: _DyingClient())
    monkeypatch.setenv("AGENT_WORKDIR", str(tmp_path))
    runner.run_agent("task", max_iter=4, log_file=str(tmp_path / "r.jsonl"),
                     system_prompt="s", workdir=str(tmp_path))
    out = capsys.readouterr().out
    assert "TOKENS: prompt=10 completion=50 total=60" in out
    assert "API_ERRORS: 3" in out


def test_runner_reports_zero_api_errors_on_clean_run(tmp_path, monkeypatch, capsys):
    import runner

    class _Done:
        chat = completions = None

        def __init__(self):
            self.chat = self
            self.completions = self

        def create(self, model, messages, tools, temperature, **kw):
            fn = type("F", (), {"name": "task_done", "arguments": '{"summary": "ok"}'})()
            tc = type("T", (), {"id": "1", "function": fn})()
            msg = type("M", (), {"content": "", "tool_calls": [tc]})()
            return type("R", (), {
                "choices": [type("C", (), {"message": msg, "finish_reason": "tool_calls"})()],
                "usage": None})()

    monkeypatch.setattr(runner, "OpenAI", lambda **kw: _Done())
    monkeypatch.setenv("AGENT_WORKDIR", str(tmp_path))
    runner.run_agent("task", max_iter=2, log_file=str(tmp_path / "r.jsonl"),
                     system_prompt="s", workdir=str(tmp_path))
    assert "API_ERRORS: 0" in capsys.readouterr().out


# --- run_eval: parsing + the cache predicate -------------------------------

def test_parse_api_errors(tmp_path):
    run_eval, _ = _fresh(tmp_path)
    assert run_eval._parse_api_errors("TOKENS: prompt=1 completion=2 total=3\n"
                                      "COMPACTIONS: 0\nAPI_ERRORS: 7\n") == 7
    # Older runner / runner killed before its summary: count the event lines.
    assert run_eval._parse_api_errors("  API error: Connection error.\n"
                                      "[iter 3/4]\n  API error: Connection error.\n") == 2
    assert run_eval._parse_api_errors("TOKENS: prompt=1 completion=2 total=3\n") == 0


def test_cacheable_result_refuses_infra_failures(tmp_path):
    run_eval, _ = _fresh(tmp_path)
    base = {"passed": False, "agent_exit_code": 0, "tokens_completion": 5123,
            "validation_output": "assertion failed: expected 3 got 4"}
    assert run_eval.cacheable_result(base) is True            # a real FAIL banks
    assert run_eval.cacheable_result({**base, "api_errors": 12}) is False
    assert run_eval.cacheable_result({**base, "reason": "server_error"}) is False
    assert run_eval.cacheable_result({**base, "reason": "env_error"}) is False
    for text in ("/bin/sh: fork: retry: Resource temporarily unavailable",
                 "error: unable to spawn LLD: SystemResources",
                 "thread constructor failed: Resource temporarily unavailable",
                 "OpenBLAS blas_thread_init: pthread_create failed for thread 3 of 32",
                 "RuntimeError: can't start new thread",
                 "error: unable to write to cache: NoSpaceLeft",
                 "cp: error writing 'x': No space left on device",
                 "OSError: [Errno 28] No space left",
                 "write failed: Disk quota exceeded"):
        assert run_eval.cacheable_result({**base, "validation_output": text}) is False, text
    # A PASS is a genuine verdict even when errors were seen on the way.
    assert run_eval.cacheable_result({**base, "passed": True, "api_errors": 3}) is True
    # The pre-existing rules still hold.
    assert run_eval.cacheable_result({**base, "agent_exit_code": -1}) is False
    assert run_eval.cacheable_result({**base, "tokens_completion": 0}) is False


def test_validation_keeps_env_evidence_past_truncation(tmp_path, monkeypatch):
    run_eval, _ = _fresh(tmp_path)
    noisy = "x" * 900 + "\n/bin/sh: fork: Resource temporarily unavailable\n"
    monkeypatch.setattr(run_eval, "_run_reaped", lambda *a, **k: (2, noisy))
    passed, out = run_eval.run_validation({"validation": {"type": "bash", "script": "x"}})
    assert passed is False and len(out) <= 700
    assert "Resource temporarily unavailable" in out
    # Negative control: an ordinary long failure is cut as before.
    monkeypatch.setattr(run_eval, "_run_reaped", lambda *a, **k: (2, "y" * 900))
    _, out = run_eval.run_validation({"validation": {"type": "bash", "script": "x"}})
    assert out == "y" * 500


# --- run_eval end to end: the row is recorded, flagged, and NOT cached -----

def _run(run_eval, monkeypatch, agent, validation, health=None):
    monkeypatch.setattr(run_eval, "run_agent", lambda *a, **k: dict(agent))
    monkeypatch.setattr(run_eval, "run_validation", lambda t: validation)
    monkeypatch.setattr(run_eval, "capture_server_config", lambda: {})
    monkeypatch.setattr(run_eval, "capture_gpu_info", lambda: {})
    monkeypatch.setattr(run_eval, "capture_inference_engine_info", lambda: {})
    return run_eval.run_eval(model_name="m", health_check=health,
                             recover_cb=(lambda: True) if health else None)


_AGENT = {"exit_code": 0, "elapsed_seconds": 1.0, "stdout": "", "stderr": "",
          "tokens": {"prompt": 10, "completion": 500, "total": 510},
          "iterations": 3, "compactions": 0, "api_errors": 0}


def test_connection_errors_mark_server_error_and_skip_cache(tmp_path, monkeypatch):
    run_eval, cache = _fresh(tmp_path)
    res = _run(run_eval, monkeypatch, {**_AGENT, "api_errors": 4}, (False, "FileNotFound"))
    row = res["tasks"][0]
    assert row["reason"] == "server_error" and row["api_errors"] == 4
    assert not (cache.CACHE_DIR.exists() and list(cache.CACHE_DIR.glob("*.json")))


def test_dead_server_after_task_marks_server_error(tmp_path, monkeypatch):
    run_eval, cache = _fresh(tmp_path)
    calls = []

    def health():                     # healthy before the task, dead after it
        calls.append(1)
        return len(calls) == 1

    res = _run(run_eval, monkeypatch, _AGENT, (False, "FileNotFound"), health=health)
    assert res["tasks"][0]["reason"] == "server_error"
    assert len(calls) == 2
    assert not (cache.CACHE_DIR.exists() and list(cache.CACHE_DIR.glob("*.json")))


def test_env_exhaustion_marks_env_error(tmp_path, monkeypatch):
    run_eval, cache = _fresh(tmp_path)
    res = _run(run_eval, monkeypatch, _AGENT,
               (False, "/bin/sh: fork: Resource temporarily unavailable"))
    assert res["tasks"][0]["reason"] == "env_error"
    assert not (cache.CACHE_DIR.exists() and list(cache.CACHE_DIR.glob("*.json")))


def test_genuine_fail_is_still_cached(tmp_path, monkeypatch):
    """Negative control: a clean-server, clean-validator FAIL banks."""
    run_eval, cache = _fresh(tmp_path)
    res = _run(run_eval, monkeypatch, _AGENT, (False, "expected 3 got 4"),
               health=lambda: True)
    assert "reason" not in res["tasks"][0]
    assert len(list(cache.CACHE_DIR.glob("*.json"))) == 1


# --- free-space floor (storage-02) -----------------------------------------

def _fake_usage(free_gb: float):
    import collections
    U = collections.namedtuple("U", "total used free")
    return lambda path: U(10**12, 10**12 - int(free_gb * 1e9), int(free_gb * 1e9))


def test_low_disk_floor(tmp_path, monkeypatch):
    run_eval, _ = _fresh(tmp_path)
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", _fake_usage(2.0))
    monkeypatch.delenv("OPENBEAST_EVAL_MIN_FREE_GB", raising=False)
    assert "2.0 GB free" in run_eval.low_disk()
    monkeypatch.setenv("OPENBEAST_EVAL_MIN_FREE_GB", "0")         # disabled
    assert run_eval.low_disk() is None
    monkeypatch.setenv("OPENBEAST_EVAL_MIN_FREE_GB", "1")
    assert run_eval.low_disk() is None


def test_low_disk_aborts_before_the_agent_runs(tmp_path, monkeypatch):
    run_eval, cache = _fresh(tmp_path)
    import shutil
    monkeypatch.setenv("OPENBEAST_EVAL_MIN_FREE_GB", "30")
    monkeypatch.setattr(shutil, "disk_usage", _fake_usage(3.0))
    ran = []
    monkeypatch.setattr(run_eval, "run_agent", lambda *a, **k: ran.append(1) or dict(_AGENT))
    monkeypatch.setattr(run_eval, "run_validation", lambda t: (False, "x"))
    monkeypatch.setattr(run_eval, "capture_server_config", lambda: {})
    monkeypatch.setattr(run_eval, "capture_gpu_info", lambda: {})
    monkeypatch.setattr(run_eval, "capture_inference_engine_info", lambda: {})
    res = run_eval.run_eval(model_name="m")
    assert not ran and res["tasks"][0]["reason"] == "low_disk"
    assert not (cache.CACHE_DIR.exists() and list(cache.CACHE_DIR.glob("*.json")))


# --- --cache-only replays the era of the model's last live run -------------

def _bank_rb20480(run_eval, cache, tmp_path):
    """One live-era row banked under .rb20480, plus the live results file
    that recorded the server flags."""
    task = run_eval.load_tasks(None)[0]
    key = cache.cache_key(task, "m", max_iter=3, rb="20480")
    cache.cache_put(key, {"id": task["id"], "passed": True, "elapsed_seconds": 2.0,
                          "tokens_completion": 9})
    results = tmp_path / "results"
    results.mkdir(exist_ok=True)
    (results / "eval-m-20260917-000000.json").write_text(json.dumps({
        "model_slug": "m",
        "server": {"cmdline": "llama-server --reasoning-budget 20480",
                   "reasoning_budget": "20480"}}))
    # A different model whose slug shares the prefix must not be consulted.
    (results / "eval-m-k-xl-20260918-000000.json").write_text(json.dumps({
        "model_slug": "m-k-xl",
        "server": {"cmdline": "x", "reasoning_budget": "4096"}}))


def test_cache_only_infers_rb_era_from_last_live_run(tmp_path):
    run_eval, cache = _fresh(tmp_path)
    _bank_rb20480(run_eval, cache, tmp_path)
    res = run_eval.run_eval(model_name="m", cache_only=True)
    row = res["tasks"][0]
    assert row.get("from_cache") is True and row["passed"] is True
    assert res["harness"]["rb_component"] == "20480"


def test_cache_only_explicit_rb_wins(tmp_path):
    run_eval, cache = _fresh(tmp_path)
    _bank_rb20480(run_eval, cache, tmp_path)
    res = run_eval.run_eval(model_name="m", cache_only=True, reasoning_budget="-1")
    assert res["tasks"][0]["reason"] == "skipped_cache_miss"   # uncapped era: a miss


def test_reproducible_env_error_banks_as_fail(tmp_path, monkeypatch):
    """The model's OWN program exhausting threads/processes matches the env
    text every run; unbounded, it reran live forever and kept the model off
    the board. The Nth env_error for one key banks as a plain FAIL."""
    run_eval, cache = _fresh(tmp_path)
    monkeypatch.delenv("OPENBEAST_EVAL_ENV_ERROR_BANK_AFTER", raising=False)
    exhaust = (False, "thread constructor failed: Resource temporarily unavailable")
    for _ in range(2):
        res = _run(run_eval, monkeypatch, _AGENT, exhaust)
        assert res["tasks"][0]["reason"] == "env_error"
        assert not list(cache.CACHE_DIR.glob("*.json"))
    res = _run(run_eval, monkeypatch, _AGENT, exhaust)
    row = res["tasks"][0]
    assert "reason" not in row and row["env_error_repeats"] == 3
    (banked,) = cache.CACHE_DIR.glob("*.json")
    assert not list(cache.STRIKES_DIR.glob("*.json"))    # strikes forgotten
    # And it now replays like any genuine FAIL.
    res = _run(run_eval, monkeypatch, _AGENT, (True, "unused"))
    assert res["tasks"][0]["from_cache"] is True and res["tasks"][0]["passed"] is False


def test_env_error_strikes_are_per_key(tmp_path, monkeypatch):
    """Negative control: strikes on one key never bank another."""
    run_eval, cache = _fresh(tmp_path)
    for _ in range(5):
        assert cache.env_error_strike("a.key") >= 1
    assert cache.env_error_strike("b.key") == 1
    monkeypatch.setenv("OPENBEAST_EVAL_ENV_ERROR_BANK_AFTER", "junk")
    assert run_eval.env_error_bank_after() == 3
