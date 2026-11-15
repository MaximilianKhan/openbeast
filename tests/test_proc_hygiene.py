#!/usr/bin/env python3
"""Process-hygiene regression tests for the 2026-07-07 OOM post-mortem.

The bug: subprocess timeouts with shell=True killed only the /bin/sh
wrapper, orphaning whatever the shell had spawned. Two orphaned
model-written eval programs (a runaway Brainfuck interpreter, twice) grew
to ~140 GB anonymous memory each, exhausted 122 GB RAM + 187 GB swap, and
the kernel OOM killer took down the entire terminal scope — Claude session,
smoke harness, and llama-server included.

Exercises:
  - agents/tools.py bash(): timeout SIGKILLs the whole process group
    (grandchildren included), and returns promptly instead of blocking
    on the pipe the orphan used to hold
  - evals/run_eval.py _run_reaped(): same, for validation/setup/cleanup
  - RLIMIT_AS backstop: a memory bomb dies with MemoryError instead of
    eating the box in the window before the timeout fires

Run: python3 tests/test_proc_hygiene.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agents"))
sys.path.insert(0, os.path.join(REPO, "evals"))

import tools  # agents/tools.py
import run_eval  # evals/run_eval.py

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name} {detail}")
    # Make failures REAL under pytest (a failed check must fail the test,
    # not just print). The counters + __main__ sys.exit path stay for
    # standalone `python3 tests/test_proc_hygiene.py` use.
    assert ok, f"{name} {detail}".rstrip()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _gone(pid: int, timeout: float = 5.0) -> bool:
    """True once pid is gone. A SIGKILLed grandchild is a zombie until its
    new parent reaps it; a fixed 0.5 s sleep lost that race on a loaded box.
    A grandchild the timeout really missed (sleep 300) still fails."""
    deadline = time.time() + timeout
    while _alive(pid):
        if time.time() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _grandchild_cmd(pidfile: str) -> str:
    # sh -c '<this>' spawns python3 as a grandchild that records its pid
    # and then sleeps far past the timeout.
    return (
        f"python3 -c \"import os,time; open('{pidfile}','w').write(str(os.getpid())); "
        f"os.sync if False else None; time.sleep(120)\""
    )


def test_bash_tool_reaps_grandchild():
    with tempfile.NamedTemporaryFile(suffix=".pid", delete=False) as f:
        pidfile = f.name
    t0 = time.time()
    out = tools.bash(_grandchild_cmd(pidfile), timeout=2)
    elapsed = time.time() - t0
    check("bash() reports the timeout", "timed out" in out, f"got: {out[:80]}")
    check("bash() returns promptly (no pipe hang)", elapsed < 10, f"{elapsed:.1f}s")
    with open(pidfile) as f:
        pid = int(f.read().strip())
    check("bash() timeout killed the grandchild", _gone(pid), f"pid {pid} survived")
    os.unlink(pidfile)


def test_run_reaped_reaps_grandchild():
    with tempfile.NamedTemporaryFile(suffix=".pid", delete=False) as f:
        pidfile = f.name
    t0 = time.time()
    try:
        run_eval._run_reaped(_grandchild_cmd(pidfile), timeout=2, shell=True)
        timed_out = False
    except Exception:
        timed_out = True
    elapsed = time.time() - t0
    check("_run_reaped raises on timeout", timed_out)
    check("_run_reaped returns promptly (no pipe hang)", elapsed < 10, f"{elapsed:.1f}s")
    with open(pidfile) as f:
        pid = int(f.read().strip())
    check("_run_reaped timeout killed the grandchild", _gone(pid), f"pid {pid} survived")
    os.unlink(pidfile)


class _NoPrlimit:
    """tools.resource stand-in without prlimit(2), as on macOS. With
    enforce_as=False setrlimit is a no-op too: Darwin accepts or refuses
    RLIMIT_AS but never enforces it, so that is the faithful simulation."""

    def __init__(self, enforce_as=True):
        import resource as real_resource
        self._real = real_resource
        self._enforce_as = enforce_as

    def __getattr__(self, name):
        if name == "prlimit":
            raise AttributeError(name)
        if name == "setrlimit" and not self._enforce_as:
            return lambda *a, **k: None
        return getattr(self._real, name)


def test_prlimitless_platform_still_works():
    # macOS has no prlimit(2): simulate it by hiding resource.prlimit from
    # tools and confirm bash() still runs (the pre-fix behavior was an
    # AttributeError on EVERY bash/grep call plus a leaked child) and, where
    # setrlimit(RLIMIT_AS) is enforced (Linux), the preexec_fn fallback
    # still caps the child's address space. Darwin never enforces it — see
    # test_group_guard_stops_memory_bomb_without_rlimits.
    saved = tools.resource
    tools.resource = _NoPrlimit()
    try:
        out = tools.bash("echo darwin-ok", timeout=10)
        check("bash() works without resource.prlimit", "darwin-ok" in out,
              f"got: {out[:80]}")
        if sys.platform != "linux":
            return
        out = tools.bash(
            "python3 -c \"b = bytearray(48 * 1024**3); print('ALLOC' + 'ATED')\"",
            timeout=30,
        )
        check(
            "setrlimit fallback still stops the memory bomb",
            "ALLOCATED" not in out and "MemoryError" in out,
            f"got: {out[:120]}",
        )
    finally:
        tools.resource = saved


def test_group_guard_stops_memory_bomb_without_rlimits():
    # No prlimit AND no enforced RLIMIT_AS (Darwin, really or simulated):
    # the parent-side group guard must kill a bomb that touches its memory.
    # Random pages so macOS's compressor cannot hide the RSS.
    saved = tools.resource
    if hasattr(saved, "prlimit"):
        tools.resource = _NoPrlimit(enforce_as=False)
    try:
        t0 = time.time()
        rc, out = tools.run_reaped(
            "python3 -c \"import os, time; "
            "b = bytearray(os.urandom(1 << 20)) * 768; "
            "time.sleep(20); print('SURV' + 'IVED')\"",
            30, as_limit=256 * 1024**2)
        elapsed = time.time() - t0
    finally:
        tools.resource = saved
    check("group guard kills the memory bomb",
          "SURVIVED" not in out and "memory cap" in out, f"got: {out[-160:]}")
    check("group guard acts promptly", elapsed < 10, f"{elapsed:.1f}s")


def test_rlimit_stops_memory_bomb():
    # Try to grab 48 GB in one shot — over the 32 GB RLIMIT_AS cap, so the
    # child must die with MemoryError instead of succeeding.
    # Success marker is assembled at runtime so the source line echoed in a
    # traceback can never contain it. Linux-only: RLIMIT_AS is not enforced
    # on Darwin (the group guard test covers that platform).
    if sys.platform != "linux":
        print("  SKIP: RLIMIT_AS is not enforced off Linux")
        return
    out = tools.bash(
        "python3 -c \"b = bytearray(48 * 1024**3); print('ALLOC' + 'ATED')\"",
        timeout=30,
    )
    check(
        "memory bomb dies at the rlimit instead of allocating",
        "ALLOCATED" not in out and "MemoryError" in out,
        f"got: {out[:120]}",
    )


def main():
    print("Process hygiene (2026-07-07 OOM post-mortem):")
    test_bash_tool_reaps_grandchild()
    test_run_reaped_reaps_grandchild()
    test_prlimitless_platform_still_works()
    test_group_guard_stops_memory_bomb_without_rlimits()
    test_rlimit_stops_memory_bomb()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
