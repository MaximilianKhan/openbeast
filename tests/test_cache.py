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
    # ...and the strike files with it: cache_clear() empties STRIKES_DIR, and
    # left at its import-time value that was the rig's real evals/cache/.
    cache.STRIKES_DIR = cache.CACHE_DIR / "env-strikes"
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
        # Context changed mid-process, and the memo is NOT cleared by hand:
        # the memo itself must notice (a long sweep never clears it).
        sys_prompt.write_text("v2-")  # different size, so stat moves too
        k2 = cache.cache_key(t, "m1")
        check("context change → key change", k1 != k2)
        check("stamped era follows the keys",
              cache.current_era() == cache.context_hash() == k2.rsplit(".", 1)[1])


def test_same_size_edit_still_moves_the_era(cache_dir, tmp_path, monkeypatch):
    """A same-size, same-second rewrite (the common one-character fix) must
    still invalidate the memo: the signature carries ns mtime/ctime and the
    inode, not just the size."""
    import time
    cache = fresh_cache_module(cache_dir)
    f = tmp_path / "runner.py"
    f.write_text("aaaa")
    monkeypatch.setattr(cache, "CONTEXT_FILES", [f])
    t = {"id": "t_a", "task": "x", "setup": "s", "validation": {}, "cleanup": "c"}
    k1 = cache.cache_key(t, "m1")
    time.sleep(0.01)
    f.write_text("bbbb")
    k2 = cache.cache_key(t, "m1")
    check("same-size edit → key change", k1 != k2)
    # Unchanged tree: the memo is reused (no rehash).
    calls = []
    real = cache.context_hash
    monkeypatch.setattr(cache, "context_hash", lambda: calls.append(1) or real())
    cache.cache_key(t, "m1")
    check("unchanged tree → memo reused", calls == [])


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


def test_clear_never_reaches_the_real_strike_files(cache_dir):
    """cache_clear() also empties the env-strike directory. The helper used to
    leave that pointing at the checkout's evals/cache/env-strikes."""
    cache = fresh_cache_module(cache_dir)
    real = ROOT / "evals" / "cache"
    assert real not in cache.STRIKES_DIR.parents and cache_dir in cache.STRIKES_DIR.parents
    cache.STRIKES_DIR.mkdir(parents=True)
    (cache.STRIKES_DIR / "k.json").write_text("{}")
    cache.cache_put("k1", {"passed": True})
    assert cache.cache_clear() == 1
    assert not list(cache.STRIKES_DIR.glob("*.json"))


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
    check("run_eval routes cache writes through cacheable_result",
          "cacheable_result(result)" in text)
    check("cacheable_result excludes ALL signal deaths (exit < 0), not just -1",
          "< 0" in text.split("def cacheable_result", 1)[1].split("def ", 1)[0])


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
    cache.STRIKES_DIR = cache.CACHE_DIR / "env-strikes"
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


# ---------------------------------------------------------------------------
# cacheable_result — guard against caching environmental deaths
# (2026-09-08 regression: a Ctrl-C'd sweep banked SIGTERM'd in-flight units
# as 0-token "failures" that silently replayed in the Phase A' rerun)
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
import run_eval  # noqa: E402


def _row(**kw):
    base = {"passed": True, "agent_exit_code": 0, "tokens_completion": 100}
    base.update(kw)
    return base


def test_cacheable_pass():
    assert run_eval.cacheable_result(_row())


def test_cacheable_genuine_fail():
    assert run_eval.cacheable_result(_row(passed=False, tokens_completion=5000))


def test_not_cacheable_wall_timeout():
    assert not run_eval.cacheable_result(_row(passed=False, agent_exit_code=-1, tokens_completion=0))


def test_not_cacheable_sigterm():
    assert not run_eval.cacheable_result(_row(passed=False, agent_exit_code=-15, tokens_completion=0))


def test_not_cacheable_sigkill():
    assert not run_eval.cacheable_result(_row(passed=False, agent_exit_code=-9, tokens_completion=0))


def test_not_cacheable_zero_token_fail():
    # exit 0 but the model never spoke — server crash/restart window
    assert not run_eval.cacheable_result(_row(passed=False, tokens_completion=0))


def test_not_cacheable_zero_token_fail_missing_field():
    row = _row(passed=False)
    del row["tokens_completion"]
    assert not run_eval.cacheable_result(row)


def _cache_mod():
    return importlib.import_module("cache")


def test_cache_key_legacy_shape_when_uncapped():
    # No rb component when the server is uncapped — every pre-2026-09-09
    # banked row must stay reachable.
    cache = _cache_mod()
    t = {"id": "01_x", "task": "t", "validation": "v"}
    k_legacy = cache.cache_key(t, "slug", max_iter=10)
    k_none = cache.cache_key(t, "slug", max_iter=10, rb=None)
    assert k_legacy == k_none
    assert ".rb" not in k_legacy


def test_cache_key_rb_era_component_when_capped():
    # A finite reasoning budget is its own cache era: capped and uncapped
    # rows must never replay across each other (the 2026-09-09 A/B trap).
    cache = _cache_mod()
    t = {"id": "01_x", "task": "t", "validation": "v"}
    k_off = cache.cache_key(t, "slug", max_iter=10)
    k_on = cache.cache_key(t, "slug", max_iter=10, rb="20480")
    assert k_off != k_on
    assert ".rb20480." in k_on


def test_cache_key_rb_and_diag_compose():
    cache = _cache_mod()
    t = {"id": "01_x", "task": "t", "validation": "v"}
    k = cache.cache_key(t, "slug", max_iter=10, diag="diag1-abcd1234", rb="20480")
    assert ".diag1-abcd1234.rb20480." in k


def test_cache_key_greedy_era_component():
    cache = _cache_mod()
    t = {"id": "01_x", "task": "t", "validation": "v"}
    k_off = cache.cache_key(t, "slug", max_iter=10)
    k_on = cache.cache_key(t, "slug", max_iter=10, greedy=True)
    assert k_off != k_on and ".greedy." in k_on and ".greedy" not in k_off


def test_every_signal_death_is_uncacheable_even_when_the_model_spoke():
    """`exit < 0`, not `exit == -1`. The three tests above send 0 completion
    tokens, which the zero-token rule refuses on its own, so `== -1` passed
    them all: the 2026-08-22 interrupt banked SIGTERM'd units exactly so."""
    for code in (-15, -9, -2, -1):
        row = _row(passed=False, agent_exit_code=code, tokens_completion=5)
        assert not run_eval.cacheable_result(row), code
        assert not run_eval.cacheable_result(dict(row, passed=True)), code
    # control: the same row with a clean exit is a genuine, bankable FAIL
    assert run_eval.cacheable_result(_row(passed=False, agent_exit_code=0, tokens_completion=5))
    assert run_eval.cacheable_result(_row(passed=False, agent_exit_code=1, tokens_completion=5))


def test_task_hash_ignores_runtime_underscore_keys(cache_dir):
    """`_path` embeds the checkout's absolute location: hashed, every cached
    result would miss after a repo move or in a second worktree."""
    cache = fresh_cache_module(cache_dir)
    t = {"id": "t_a", "task": "x", "setup": "s", "validation": {}, "cleanup": "c"}
    assert cache.task_hash({**t, "_path": "/a/t.yaml"}) == cache.task_hash({**t, "_path": "/b/t.yaml"})
    assert cache.task_hash({**t, "_path": "/a/t.yaml", "_loaded": 1}) == cache.task_hash(t)
    # control: a real field still changes it
    assert cache.task_hash({**t, "task": "y"}) != cache.task_hash(t)


def test_context_hash_separates_its_files(cache_dir, tmp_path, monkeypatch):
    """The same bytes split differently across two context files are two
    different contexts (a line moved from one prompt file to the other)."""
    cache = fresh_cache_module(cache_dir)
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    monkeypatch.setattr(cache, "CONTEXT_FILES", [a, b])

    def h(x, y):
        a.write_text(x)
        b.write_text(y)
        cache._context_cache.clear()
        return cache.context_hash()

    assert h("ab", "c") != h("a", "bc")
    assert h("ab", "c") == h("ab", "c")


def test_era_hashes_what_the_eval_agent_consumes(cache_dir):
    """The era must hash the files an eval unit reads and nothing else: the
    runner never reads opencode.json or system-prompt-tools.md, so a
    frontend-catalog edit must not cold-start every model's cache — and
    run_eval's agent-shaping code must be in it (via HARNESS_VERSION)."""
    cache = fresh_cache_module(cache_dir)
    names = {p.name for p in cache.CONTEXT_FILES}
    assert "opencode.json" not in names
    assert "system-prompt-tools.md" not in names
    assert "HARNESS_VERSION" in names
    for f in ("runner.py", "tools.py"):
        src = (ROOT / "agents" / f).read_text()
        assert "opencode.json" not in src and "system-prompt-tools" not in src, \
            f"agents/{f} now reads a frontend file: put it back in the era"


def test_harness_pin_matches_run_eval(cache_dir):
    """run_eval's agent-shaping code (argv, env, budgets, validator) is in
    the era only through evals/HARNESS_VERSION. A change to that code with
    a stale pin would let rows judged by different harness code replay
    together — so the pin must match the code."""
    cache = fresh_cache_module(cache_dir)
    ver, pinned = cache.pinned_harness()
    now = cache.harness_fingerprint()
    assert pinned == now, (
        f"run_eval.py's agent-shaping code changed (fingerprint {now}, pinned "
        f"{pinned}). Bump {ver} in evals/HARNESS_VERSION and set the "
        f"fingerprint to {now}: this deliberately moves the eval era.")


def test_harness_fingerprint_ignores_prose_but_sees_code(tmp_path):
    cache = fresh_cache_module(tmp_path / "c")
    src = (ROOT / "evals" / "run_eval.py").read_text()
    base = cache.harness_fingerprint()
    p = tmp_path / "run_eval.py"
    for anchor in ("Run the agent against a task.", "max_iter * 60 * timeout_scale",
                   "VALIDATION_TIMEOUT_S = 30"):
        assert anchor in src, f"test anchor gone from run_eval.py: {anchor}"
    # A comment and a docstring edit inside run_agent: no era move.
    p.write_text(src.replace('    max_iter = max_iter_override or task.get("max_iter", 15)\n',
                             '    # a new comment\n'
                             '    max_iter = max_iter_override or task.get("max_iter", 15)\n', 1)
                 .replace("Run the agent against a task.", "Run THE agent on a task.", 1))
    assert cache.harness_fingerprint(p) == base
    # The wall budget changes: the fingerprint moves.
    p.write_text(src.replace("max_iter * 60 * timeout_scale", "max_iter * 90 * timeout_scale", 1))
    assert cache.harness_fingerprint(p) != base
    # So does the validator timeout.
    p.write_text(src.replace("VALIDATION_TIMEOUT_S = 30", "VALIDATION_TIMEOUT_S = 45", 1))
    assert cache.harness_fingerprint(p) != base


def test_harness_fingerprint_sees_the_jobs_contention_scale(tmp_path):
    """The --jobs wall-budget multiplier multiplies every parallel agent's
    budget and validator timeout; it was computed inline in run_eval(),
    outside every fingerprinted function, so changing it moved no era
    (verifier finding on campaign-evals: fingerprint omits timeout_scale)."""
    cache = fresh_cache_module(tmp_path / "c")
    src = (ROOT / "evals" / "run_eval.py").read_text()
    anchor = "1.0 + (jobs - 1) / 3.0"
    assert anchor in src, f"test anchor gone from run_eval.py: {anchor}"
    p = tmp_path / "run_eval.py"
    p.write_text(src.replace(anchor, "1.0 + (jobs - 1) / 2.0", 1))
    assert cache.harness_fingerprint(p) != cache.harness_fingerprint()
