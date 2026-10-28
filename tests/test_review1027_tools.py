"""Tool fixes from the 2026-10-27 review (agents/tools.py, eval-era file).

R1-02  list_files never follows a directory symlink and is bounded.
R1-03  edit_file refuses files over read_file's size cap.
R1-06  the bash tool kills everything the command backgrounded, redirected
       or nohup'd jobs included, as its description says.
R1-08  fetch returns `Error:` on a malformed URL or max_length, never raises.
R6-11  under eval, the file tools refuse OPENBEAST_EVAL_DENY_ROOTS.
"""

import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))

import tools  # noqa: E402


# --------------------------------------------------------------------------
# R1-06
# --------------------------------------------------------------------------

def _alive(pid: int) -> bool:
    # Not pgrep: conftest stubs it to "nothing found".
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def _wait_dead(pid: int, secs: float = 3.0) -> bool:
    deadline = time.monotonic() + secs
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _alive(pid)


@pytest.mark.parametrize("template", [
    "sleep 300 >/dev/null 2>&1 & echo $! > {pf}; echo started",
    "nohup sleep 300 >/dev/null 2>&1 & echo $! > {pf}; echo started",
    "sleep 300 & echo $! > {pf}; echo started",
])
def test_bash_kills_what_it_backgrounded(tmp_path, template):
    pf = tmp_path / "pid"
    t = time.monotonic()
    out = tools.bash(template.format(pf=pf), 20)
    took = time.monotonic() - t
    pid = int(pf.read_text())
    try:
        assert "started" in out
        assert took < 4, f"waited {took:.1f}s for the background job"
        assert _wait_dead(pid), "a backgrounded job outlived the bash call"
    finally:
        if _alive(pid):
            os.kill(pid, 9)


def test_run_reaped_default_keeps_old_contract(tmp_path):
    # Negative control. Only the bash tool reaps the group; other callers
    # (setup, validators) keep the old behaviour, so a fixture can still
    # start a detached job.
    pf = tmp_path / "pid"
    rc, _ = tools.run_reaped(f"sleep 300 >/dev/null 2>&1 & echo $! > {pf}", 10)
    pid = int(pf.read_text())
    try:
        assert rc == 0 and _alive(pid)
    finally:
        os.kill(pid, 9)


# --------------------------------------------------------------------------
# R1-08
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["http://example.com:99999/", "http://[::1/",
                                 "http://a:b/"])
def test_fetch_malformed_url_is_an_error_string(url):
    out = tools.fetch(url)
    assert out.startswith("Error: invalid URL")


@pytest.mark.parametrize("bad", ["abc", None, float("inf")])
def test_fetch_bad_max_length_is_an_error_string(bad, monkeypatch):
    # The URL check passes without DNS for a literal public IP.
    monkeypatch.setattr(tools, "_fetch_body",
                        lambda *a, **k: pytest.fail("must not fetch"))
    out = tools.fetch("http://93.184.215.14/", max_length=bad)
    assert out.startswith("Error: max_length")


