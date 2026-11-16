"""Review 1116, v5 families b: pins for the fixes to the U3 upgrades (36, 40,
122, 123), the new families 183/188, the kept-v4 substitutions and the
shared validator library.

Each test names the finding it pins. They run without a model and without a
compiler unless the test says so (those skip when the toolchain is absent).
"""

import importlib
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "tests"))

vlib = importlib.import_module("vlib")

DARWIN = sys.platform == "darwin"


# --- the process sampler on macOS (finding: `ps -A` forked every 50 ms poll) ---

@pytest.mark.skipif(not DARWIN, reason="libproc is the macOS sampler")
def test_libproc_table_agrees_with_ps():
    lp = vlib._libproc_table()
    assert lp, "libproc table unavailable on macOS"
    ps = vlib._ps_table()
    me = os.getpid()
    assert lp[me][:2] == ps[me][:2] == (os.getppid(), os.getpgrp())
    common = set(lp) & set(ps)
    # every process ps sees (all uids) is listed, with the same ppid and pgid
    assert len(common) >= 0.95 * len(ps)
    assert all(lp[p][:2] == ps[p][:2] for p in common)
    assert 1 in lp, "another uid's process (launchd) must be listed too"


@pytest.mark.skipif(not DARWIN, reason="libproc is the macOS sampler")
def test_libproc_cpu_is_seconds_not_mach_ticks():
    t0 = time.process_time()
    while time.process_time() - t0 < 0.3:
        pass
    got = vlib._libproc_table()[os.getpid()][2]
    assert abs(got - time.process_time()) < 0.1


@pytest.mark.skipif(not DARWIN, reason="libproc is the macOS sampler")
def test_processes_does_not_fork_ps_on_macos(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("processes() forked ps although libproc works")
    monkeypatch.setattr(vlib, "_ps_table", boom)
    assert os.getpid() in vlib.processes()
    r = vlib.run_solution(["/bin/sh", "-c", "exit 0"], cpu_s=5, allow_fork=True)
    assert r.verdict == "ok"


def test_processes_falls_back_to_ps_when_libproc_is_missing(monkeypatch):
    monkeypatch.setattr(vlib, "_LIBPROC", False)
    monkeypatch.setattr(vlib, "_LINUX", False)
    tab = vlib.processes()
    assert os.getpid() in tab
