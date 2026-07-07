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


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
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
    time.sleep(0.5)
    with open(pidfile) as f:
        pid = int(f.read().strip())
    check("bash() timeout killed the grandchild", not _alive(pid), f"pid {pid} survived")
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
    time.sleep(0.5)
    with open(pidfile) as f:
        pid = int(f.read().strip())
    check("_run_reaped timeout killed the grandchild", not _alive(pid), f"pid {pid} survived")
    os.unlink(pidfile)


def test_rlimit_stops_memory_bomb():
    # Try to grab 48 GB in one shot — over the 32 GB RLIMIT_AS cap, so the
    # child must die with MemoryError instead of succeeding.
    # Success marker is assembled at runtime so the source line echoed in a
    # traceback can never contain it.
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
    test_rlimit_stops_memory_bomb()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
