#!/usr/bin/env python3
"""Pytest fixtures shared across the test suite."""

import os
import stat
import tempfile

import pytest

# --- host probes -------------------------------------------------------------
# Code under test asks the HOST what it is: evals/run_eval.py runs nvidia-smi
# and pgreps for a llama-server to stamp provenance, agents/artifact.py asks
# `tailscale serve status` who owns the tailnet name. Un-stubbed, a test's
# outcome depended on whether Max's stack was up while it ran, and a full run
# made real GPU queries and process-table scans on the rig. So for the whole
# session these three names resolve to stubs that answer "nothing here" (exit
# 1, no output) and record the call in $OPENBEAST_TEST_PROBE_LOG.
#
# A test that prepends its own stub dir to PATH still wins. A test that needs
# the real tool says so: @pytest.mark.host_probes.
HOST_PROBES = ("nvidia-smi", "pgrep", "tailscale")
_PROBE_DIR = tempfile.mkdtemp(prefix="ob-test-probes-")
_REAL_PATH = os.environ.get("PATH", "")


def _install_probe_stubs() -> None:
    log = os.path.join(_PROBE_DIR, "calls.log")
    for name in HOST_PROBES:
        f = os.path.join(_PROBE_DIR, "bin", name)
        os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f, "w") as fh:
            fh.write("#!/bin/sh\n"
                     f'echo "{name} $* [${{PYTEST_CURRENT_TEST:-?}}]" >> "${{OPENBEAST_TEST_PROBE_LOG:-/dev/null}}"\n'
                     "exit 1\n")
        os.chmod(f, os.stat(f).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.environ.setdefault("OPENBEAST_TEST_PROBE_LOG", log)   # a caller may keep the log
    os.environ["PATH"] = os.path.join(_PROBE_DIR, "bin") + os.pathsep + _REAL_PATH


_install_probe_stubs()

# --- eval host provisioning ----------------------------------------------------
# run_eval / profile / benchmark_all main() install missing eval toolchains
# (scripts/eval-host.sh setup: hundreds of MB into ~/.local/opt) and re-exec
# under the eval venv. A test run must never do either, so for the whole
# session provisioning is off: they only check and report. Tests of the
# provisioning itself (tests/test_eval_host.py) delete the variable and point
# the script at a temp prefix and a fake lock.
os.environ["OPENBEAST_EVAL_NO_PROVISION"] = "1"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "host_probes: run with the real nvidia-smi / pgrep / tailscale "
                   "on PATH (the default is a stub that finds nothing)")


def pytest_unconfigure(config):
    import shutil
    shutil.rmtree(_PROBE_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _host_probes(request, monkeypatch):
    """Opt out of the probe stubs for one test (@pytest.mark.host_probes)."""
    if request.node.get_closest_marker("host_probes"):
        stub = os.path.join(_PROBE_DIR, "bin")
        monkeypatch.setenv("PATH", os.pathsep.join(
            p for p in os.environ.get("PATH", "").split(os.pathsep) if p != stub))


@pytest.fixture
def cache_dir(tmp_path):
    """Provide an isolated cache directory for each test."""
    d = tmp_path / "cache"
    d.mkdir()
    return d


@pytest.fixture
def td(tmp_path):
    """Provide an isolated temp directory (alias used by health-recovery tests)."""
    return tmp_path


@pytest.fixture(autouse=True)
def _isolated_tool_audit(tmp_path, monkeypatch):
    """Every test gets its own tool-call audit file.

    agents/openapi_tools.py appends a row per call to the rig's REAL security
    audit trail (.run/tool-audit.jsonl) unless told otherwise, and the suite
    drives it with forged users, denied /bash probes and "../../etc" ids —
    thousands of fixture rows an operator could not tell from an attack. A
    test that wants to read the rows reads $OPENBEAST_TOOL_AUDIT_PATH.
    """
    monkeypatch.setenv("OPENBEAST_TOOL_AUDIT_PATH",
                       str(tmp_path / "tool-audit" / "tool-audit.jsonl"))


# --- child subreaper ---------------------------------------------------------
# vlib.run_solution makes ITS CALLER a child subreaper on Linux, so a
# validator keeps its solution's orphans in view. Called in-process by the
# eval isolation tests, that turned the whole pytest process into a
# subreaper: every later test's orphaned grandchild became pytest's unreaped
# zombie, signal 0 still found it, and the reap tests in test_proc_hygiene,
# test_tools and test_steering failed only in a full, ordered run. Undo it
# after every test.
@pytest.fixture(autouse=True)
def _no_lingering_subreaper():
    yield
    import sys
    if not sys.platform.startswith("linux"):
        return
    mod = sys.modules.get("vlib")
    if mod is None or not getattr(mod, "_SUBREAPER", None):
        return
    try:
        import ctypes
        ctypes.CDLL(None).prctl(36, 0, 0, 0, 0)     # PR_SET_CHILD_SUBREAPER off
    except OSError:
        pass
    mod._SUBREAPER = None


# --- harness modules re-imported by a test ----------------------------------
# Several eval tests pop run_eval / cache from sys.modules, re-import them and
# point the fresh module at a temp tree (TASKS_DIR, RESULTS_DIR, CACHE_DIR)
# without undoing it. A later test that imports run_eval lazily then got that
# module and saw an empty task library (test_fast_suite failed in a full
# ordered run, never alone). Put the module objects back after every test.
_HARNESS_MODULES = ("run_eval", "cache", "suite_manifest", "scoring", "make_fast_suite")


_HARNESS_PATHS = ("TASKS_DIR", "RESULTS_DIR", "SUITES_DIR", "CACHE_DIR", "STRIKES_DIR")


@pytest.fixture(autouse=True)
def _restore_harness_modules():
    # tests/prove_task.py --tasks-dir also sets run_eval.TASKS_DIR on the
    # shared module itself, so the path attributes are restored as well.
    import sys
    saved = {k: sys.modules.get(k) for k in _HARNESS_MODULES}
    paths = {k: {a: getattr(m, a) for a in _HARNESS_PATHS if hasattr(m, a)}
             for k, m in saved.items() if m is not None}
    yield
    for k, mod in saved.items():
        if mod is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = mod
            for a, v in paths[k].items():
                setattr(mod, a, v)
