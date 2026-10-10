#!/usr/bin/env python3
"""The suite's own isolation (tests/conftest.py), so it cannot rot unseen.

Run: python3 -m pytest tests/test_suite_hermetic.py -q
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

import conftest


@pytest.mark.parametrize("tool", conftest.HOST_PROBES)
def test_host_probes_are_stubs_that_find_nothing_and_say_who_asked(tool):
    path = shutil.which(tool)
    assert path and path.startswith(conftest._PROBE_DIR), path
    log = os.environ["OPENBEAST_TEST_PROBE_LOG"]
    before = open(log).read() if os.path.exists(log) else ""
    r = subprocess.run([tool, "--probe-check"], capture_output=True, text=True)
    assert r.returncode == 1 and r.stdout == ""
    new = open(log).read()[len(before):]
    assert f"{tool} --probe-check" in new and "test_suite_hermetic.py" in new


@pytest.mark.host_probes
def test_the_marker_puts_the_real_tools_back():
    """Negative control: opted out, nothing resolves to a stub."""
    for tool in conftest.HOST_PROBES:
        path = shutil.which(tool)
        assert path is None or not path.startswith(conftest._PROBE_DIR), path


def test_run_eval_provenance_sees_no_gpu_and_no_server(monkeypatch):
    """What the stubs are for: evals/run_eval.py's host captures answer from
    the stubs, whatever is running on this box."""
    import sys
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "evals"))
    sys.modules.pop("run_eval", None)
    import run_eval
    assert run_eval.capture_gpu_info() == {}
    assert run_eval.capture_server_config("http://localhost:8080/v1") == {}
