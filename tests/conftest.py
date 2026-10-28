#!/usr/bin/env python3
"""Pytest fixtures shared across the test suite."""

import os
import re
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


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "host_probes: run with the real nvidia-smi / pgrep / tailscale "
                   "on PATH (the default is a stub that finds nothing)")


def pytest_unconfigure(config):
    import shutil
    shutil.rmtree(_PROBE_DIR, ignore_errors=True)


# --- skip gate (CI) -----------------------------------------------------------
# The shell suites turn "SKIP … exit 0" into a failure under CI=true; pytest had
# no equivalent, and CI ran with -q, so its log said "8 skipped" and never
# which. Dozens of tests skip when chrome or zig is missing, so a runner image
# that dropped chrome, or a zig step whose PATH broke, would go green.
#
# Under CI=true a skip whose reason matches none of the patterns below FAILS the
# session (it is listed, with ::error annotations on GitHub). The allowlist is
# the skips that are correct on a CI runner: the network is off by policy,
# there is no llama-server build, and the runner's toolchain versions are not
# the rig's. A missing tool CI installs (chrome, zig, go, gcc, node, jq, a
# python dependency) is NOT on it — that is exactly what this catches.
# OPENBEAST_SKIP_GATE=off|warn|strict overrides (default: strict under
# CI=true, off otherwise).
SKIP_ALLOWLIST = (
    r"network tests disabled",
    r"llama-server not built here",
    r"toolchain is not the indexed version",
    r"no indexed toolchain here",
    r"too few toolchains here to measure",
    r"could not reproduce low pipe fds here",
    r"every candidate module is importable on this platform",
    r"kernel does not report a boot id",
    r"cannot make a file unreadable here",
)
_UNEXPECTED_SKIPS: list = []


def _skip_gate_mode() -> str:
    v = os.environ.get("OPENBEAST_SKIP_GATE", "").strip().lower()
    if v in ("off", "warn", "strict"):
        return v
    return "strict" if os.environ.get("CI") == "true" else "off"


def _skip_reason(report) -> str:
    lr = report.longrepr
    reason = lr[2] if isinstance(lr, tuple) and len(lr) == 3 else str(lr)
    return reason[len("Skipped: "):] if reason.startswith("Skipped: ") else reason


def pytest_runtest_logreport(report):
    if report.skipped and not hasattr(report, "wasxfail"):
        reason = _skip_reason(report)
        if not any(re.search(p, reason) for p in SKIP_ALLOWLIST):
            _UNEXPECTED_SKIPS.append((report.nodeid, reason))


def pytest_sessionfinish(session, exitstatus):
    if _UNEXPECTED_SKIPS and _skip_gate_mode() == "strict" and exitstatus == 0:
        session.exitstatus = 1


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    mode = _skip_gate_mode()
    if mode == "off" or not _UNEXPECTED_SKIPS:
        return
    tr = terminalreporter
    tr.section(f"skips outside the CI allowlist ({mode}; tests/conftest.py SKIP_ALLOWLIST)")
    level = "error" if mode == "strict" else "warning"
    for nodeid, reason in _UNEXPECTED_SKIPS:
        tr.write_line(f"{nodeid}: {reason}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            tr.write_line(f"::{level}::skipped in CI: {nodeid}: {reason}")
    if mode == "strict":
        tr.write_line(f"{len(_UNEXPECTED_SKIPS)} unexpected skip(s): the session FAILS under CI=true")


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
