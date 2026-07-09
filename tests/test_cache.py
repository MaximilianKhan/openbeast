#!/usr/bin/env python3
"""End-to-end durability / correctness checks for the eval cache.

Exercises:
  - put / get round-trip
  - cache key stability (same input → same key)
  - cache key invalidation on (a) task spec change, (b) model change,
    (c) agent context change
  - atomic writes (tmp + rename — no torn writes if killed mid-write)
  - file durability (cross-process: write in subprocess, read in parent)
  - corrupt-file safety (cache_get returns None instead of crashing)
  - timeout-skip policy (run_eval should NOT cache exit_code == -1)

Run: python3 tests/test_cache.py
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))


def fresh_cache_module(cache_dir: Path):
    """Return a fresh `cache` module pointing at cache_dir.

    We import then monkey-patch CACHE_DIR so each test gets isolation."""
    if "cache" in sys.modules:
        del sys.modules["cache"]
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = cache_dir
    cache._context_cache.clear()
    return cache


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
    # Make failures REAL under pytest (a failed check must fail the test,
    # not just print). The counters + __main__ sys.exit path stay for
    # standalone `python3 tests/test_cache.py` use.
    assert cond, f"{label}  {detail}".rstrip()


def test_put_get_roundtrip(cache_dir):
    cache = fresh_cache_module(cache_dir)
    task = {"id": "t_a", "task": "do thing", "setup": "x", "validation": {}, "cleanup": "y"}
    key = cache.cache_key(task, "test-model")
    cache.cache_put(key, {"id": "t_a", "passed": True, "elapsed_seconds": 1.5})
    got = cache.cache_get(key)
    check("put/get round-trip", got is not None and got.get("passed") is True, str(got))
    check("cached_at populated", got is not None and "cached_at" in got)


def test_key_stability(cache_dir):
    cache = fresh_cache_module(cache_dir)
    task = {"id": "t_a", "task": "x", "setup": "s", "validation": {}, "cleanup": "c"}
    k1 = cache.cache_key(task, "m1")
    k2 = cache.cache_key(task, "m1")
    check("same input → same key", k1 == k2)


def test_key_invalidation_on_task_change(cache_dir):
    cache = fresh_cache_module(cache_dir)
    t1 = {"id": "t_a", "task": "x", "setup": "s", "validation": {}, "cleanup": "c"}
    t2 = dict(t1, task="y")  # spec changed
    k1 = cache.cache_key(t1, "m1")
    k2 = cache.cache_key(t2, "m1")
    check("task change → key change", k1 != k2)


def test_key_invalidation_on_model_change(cache_dir):
    cache = fresh_cache_module(cache_dir)
    t = {"id": "t_a", "task": "x", "setup": "s", "validation": {}, "cleanup": "c"}
    k1 = cache.cache_key(t, "m1")
    k2 = cache.cache_key(t, "m2")
    check("model change → key change", k1 != k2)


def test_key_invalidation_on_context_change(cache_dir):
    """Modify a context file in a temp dir, point cache at it, see key change."""
    cache = fresh_cache_module(cache_dir)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        sys_prompt = td / "system-prompt.md"
        tools = td / "system-prompt-tools.md"
        opencode = td / "opencode.json"
        sys_prompt.write_text("v1")
        tools.write_text("tools-v1")
        opencode.write_text("{}")
        cache.CONTEXT_FILES = [sys_prompt, tools, opencode]
        cache._context_cache.clear()
        t = {"id": "t_a", "task": "x", "setup": "s", "validation": {}, "cleanup": "c"}
        k1 = cache.cache_key(t, "m1")
        sys_prompt.write_text("v2")  # context changed
        cache._context_cache.clear()  # force recompute
        k2 = cache.cache_key(t, "m1")
        check("context change → key change", k1 != k2)


def test_cross_process_durability(cache_dir):
    """Write in subprocess, read back in parent process."""
    cache = fresh_cache_module(cache_dir)
    task = {"id": "t_a", "task": "x", "setup": "s", "validation": {}, "cleanup": "c"}
    key = cache.cache_key(task, "test-model")
    # Write in a subprocess
    code = f"""
import sys; sys.path.insert(0, {str(ROOT / 'evals')!r})
import cache
cache.CACHE_DIR = {str(cache_dir)!r}
import pathlib; cache.CACHE_DIR = pathlib.Path(cache.CACHE_DIR)
cache.cache_put({key!r}, {{'id': 't_a', 'passed': True, 'elapsed_seconds': 99.9}})
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if r.returncode != 0:
        check("subprocess write succeeded", False, r.stderr)
        return
    # Parent reads it
    got = cache.cache_get(key)
    check("cross-process durability (file is file)",
          got is not None and got.get("elapsed_seconds") == 99.9, str(got))


def test_atomic_write(cache_dir):
    """Verify the .tmp file is removed after successful rename."""
    cache = fresh_cache_module(cache_dir)
    task = {"id": "t_a", "task": "x", "setup": "s", "validation": {}, "cleanup": "c"}
    key = cache.cache_key(task, "m1")
    cache.cache_put(key, {"id": "t_a", "passed": True, "elapsed_seconds": 1})
    tmp_files = list(cache_dir.glob("*.tmp"))
    check("no leftover .tmp file after put", not tmp_files, [str(p) for p in tmp_files])
    final = cache.cache_path(key)
    check("final file exists at expected path", final.exists())


def test_corrupt_file_safety(cache_dir):
    cache = fresh_cache_module(cache_dir)
    task = {"id": "t_a", "task": "x", "setup": "s", "validation": {}, "cleanup": "c"}
    key = cache.cache_key(task, "m1")
    p = cache.cache_path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not valid json")
    got = cache.cache_get(key)
    check("corrupt file → cache_get returns None (no crash)", got is None)


def test_invalidate_model(cache_dir):
    cache = fresh_cache_module(cache_dir)
    t = {"id": "t_a", "task": "x", "setup": "s", "validation": {}, "cleanup": "c"}
    cache.cache_put(cache.cache_key(t, "ma"), {"x": 1})
    cache.cache_put(cache.cache_key(t, "mb"), {"x": 1})
    cache.cache_put(cache.cache_key(dict(t, id="t_b"), "ma"), {"x": 1})
    n = cache.cache_invalidate_model("ma")
    check("cache_invalidate_model removes only that model's entries", n == 2,
          f"removed {n}, expected 2")
    check("the other model's entries survive", cache.cache_get(cache.cache_key(t, "mb")) is not None)


def test_clear(cache_dir):
    cache = fresh_cache_module(cache_dir)
    t = {"id": "t_a", "task": "x", "setup": "s", "validation": {}, "cleanup": "c"}
    cache.cache_put(cache.cache_key(t, "m1"), {"x": 1})
    cache.cache_put(cache.cache_key(t, "m2"), {"x": 1})
    n = cache.cache_clear()
    check("cache_clear removes all", n == 2)
    check("cache empty after clear", cache.cache_stats()["entries"] == 0)


def test_key_invalidation_on_max_iter_change(cache_dir):
    """The effective iteration budget is part of the key: a 5-iter capped
    run is a different experiment from a 15-iter one."""
    cache = fresh_cache_module(cache_dir)
    t = {"id": "t_a", "task": "x", "setup": "s", "validation": {}, "cleanup": "c"}
    k5 = cache.cache_key(t, "m1", max_iter=5)
    k15 = cache.cache_key(t, "m1", max_iter=15)
    k_legacy = cache.cache_key(t, "m1")
    check("max_iter change → key change", k5 != k15)
    check("max_iter=None keeps the legacy key shape (no .mi segment)",
          ".mi" not in k_legacy)


def test_suite_version_in_cache_key(cache_dir):
    """SUITE_VERSION is part of the agent context: bumping the suite marker
    must invalidate every cached result (the task set was redefined)."""
    cache = fresh_cache_module(cache_dir)
    # Non-tautological half: the module's OWN default CONTEXT_FILES must
    # include evals/SUITE_VERSION (and the agent runtime files).
    names = {p.name for p in cache.CONTEXT_FILES}
    check("SUITE_VERSION is in the default CONTEXT_FILES",
          "SUITE_VERSION" in names, str(sorted(names)))
    check("agent runtime (runner.py + tools.py) is in the default CONTEXT_FILES",
          {"runner.py", "tools.py"} <= names, str(sorted(names)))
    # Behavioral half: changing the SUITE_VERSION content changes the key.
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        sv = td / "SUITE_VERSION"
        sv.write_text("v4\n")
        cache.CONTEXT_FILES = [
            (sv if p.name == "SUITE_VERSION" else p) for p in cache.CONTEXT_FILES
        ]
        cache._context_cache.clear()
        t = {"id": "t_a", "task": "x", "setup": "s", "validation": {}, "cleanup": "c"}
        k1 = cache.cache_key(t, "m1")
        sv.write_text("v5\n")  # suite bump
        cache._context_cache.clear()
        k2 = cache.cache_key(t, "m1")
        check("SUITE_VERSION change → key change", k1 != k2)


def test_run_eval_skips_timeout_cache():
    """Read run_eval.py source and verify the timeout-skip guard is present.

    Source-grep is intentionally kept alongside the behavioral test below:
    it pins the exact guard expression so a refactor that renames the field
    (silently disabling the behavioral path) still trips something."""
    text = (ROOT / "evals" / "run_eval.py").read_text()
    check("run_eval guards against caching timeouts (agent_exit_code != -1)",
          "agent_exit_code\") != -1" in text or "agent_exit_code') != -1" in text)


def test_run_eval_timeout_not_cached_behavioral(td):
    """Behavioral twin of the source-grep above: run run_eval with a fake
    agent that 'times out' (exit_code == -1) and assert nothing lands in
    the cache directory."""
    cache_dir = td / "cache"; cache_dir.mkdir()
    results_dir = td / "results"; results_dir.mkdir()
    tasks_dir = td / "tasks"; tasks_dir.mkdir()
    (tasks_dir / "01_timeout.json").write_text(json.dumps({
        "id": "01_timeout", "name": "01_timeout", "difficulty": "easy",
        "task": "noop", "setup": "true",
        "validation": {"type": "bash", "script": "true"},
        "cleanup": "true", "max_iter": 1,
    }))
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = cache_dir
    cache._context_cache.clear()
    run_eval = importlib.import_module("run_eval")
    run_eval.RESULTS_DIR = str(results_dir)
    run_eval.TASKS_DIR = str(tasks_dir)
    run_eval.run_agent = lambda *a, **kw: {
        "exit_code": -1,  # timeout marker
        "elapsed_seconds": 0.1, "stdout": "", "stderr": "",
        "tokens": {"prompt": 0, "completion": 0, "total": 0},
    }
    results = run_eval.run_eval(model_name="fake-model", use_cache=True)
    check("timeout run recorded with agent_exit_code == -1",
          results["tasks"][0].get("agent_exit_code") == -1,
          str(results["tasks"][:1]))
    cached_files = list(cache_dir.glob("*.json"))
    check("timeout result NOT written to cache",
          len(cached_files) == 0, f"found {len(cached_files)} cache files")


def test_cache_dir_is_local_filesystem():
    """Sanity: the MODULE DEFAULT cache dir resolves under the repo root,
    not /tmp or similar. Imported fresh WITHOUT the fixture's CACHE_DIR
    patch so we assert the real default, not a value we injected."""
    sys.modules.pop("cache", None)
    cache = importlib.import_module("cache")
    try:
        default_dir = Path(cache.CACHE_DIR).resolve()
        check("default cache dir is under repo (not /tmp)",
              str(default_dir).startswith(str(ROOT)), str(default_dir))
        check("default cache dir is evals/cache",
              default_dir == (ROOT / "evals" / "cache").resolve(), str(default_dir))
    finally:
        # Don't leak the unpatched module (pointing at the REAL repo cache)
        # into later tests — they must re-import via fresh_cache_module.
        sys.modules.pop("cache", None)


def main():
    with tempfile.TemporaryDirectory() as td:
        cd = Path(td) / "cache"
        cd.mkdir()
        for fn in [
            test_put_get_roundtrip,
            test_key_stability,
            test_key_invalidation_on_task_change,
            test_key_invalidation_on_model_change,
            test_key_invalidation_on_context_change,
            test_cross_process_durability,
            test_atomic_write,
            test_corrupt_file_safety,
            test_invalidate_model,
            test_clear,
            test_key_invalidation_on_max_iter_change,
            test_suite_version_in_cache_key,
        ]:
            # Reset cache dir between tests.
            for p in cd.glob("*"):
                p.unlink() if p.is_file() else shutil.rmtree(p)
            print(f"\n{fn.__name__}:")
            try:
                fn(cd)
            except Exception as e:
                global FAILED
                FAILED += 1
                print(f"  [CRASH] {fn.__name__}: {e}")

    # Tests that don't need an isolated cache dir
    def _standalone(fn, *args):
        global FAILED
        print(f"\n{fn.__name__}:")
        try:
            fn(*args)
        except Exception as e:
            FAILED += 1
            print(f"  [CRASH] {fn.__name__}: {e}")

    _standalone(test_run_eval_skips_timeout_cache)
    with tempfile.TemporaryDirectory() as t:
        _standalone(test_run_eval_timeout_not_cached_behavioral, Path(t))
    _standalone(test_cache_dir_is_local_filesystem)

    print(f"\n{'='*40}")
    print(f"Cache tests: {PASSED} passed, {FAILED} failed")
    print(f"{'='*40}")
    sys.exit(0 if FAILED == 0 else 1)


if __name__ == "__main__":
    main()
