"""190_autograd_mlp: the confined driver, with the lint taken away.

The validator's AST lint is defence in depth; the boundary is the confined
driver process (vlib.run_solution confine=[work], read_only=python_runtime).
The prover's cheat and leak arms all die on the lint first, so on their own
they prove nothing about that boundary. These tests strip the lint from the
REAL validator (one line, asserted to be there) and run the same arms
through the REAL run_eval lifecycle:

  * the reference still passes (the stripped validator is otherwise intact);
  * wrong_cheat_procinfo.py, a full-power probe (ctypes, os, glob,
    subprocess, a restored site-packages), finds no oracle text in the argv
    or environment of any ancestor process, no task JSON / oracle / .git of
    any checkout, no torch or numpy, and no child process, so it raises on
    import: `import failed: RuntimeError`.
    On macOS sandbox-exec does NOT mediate sysctl kern.procargs2 for a
    same-uid process, so every ancestor's argv and environment stay
    readable to a confined solution. That is safe only because the harness
    feeds the validator on stdin with a scrubbed env and the program seed
    lives only in validator memory. This test is what pins that invariant.
  * every other cheat arm fails for a reason that is not the lint.

Each run takes a few seconds; the unit's /tmp/eval_autograd190 is shared
with the prover, so do not run both at once.
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
REFS = REPO / "evals" / "refs" / "190_autograd_mlp"
TASK = REPO / "evals" / "tasks" / "190_autograd_mlp.json"
LINT_LINE = "problems = lint(src)\n"

pytestmark = pytest.mark.skipif(
    not (sys.platform == "darwin" and os.path.exists("/usr/bin/sandbox-exec"))
    and not sys.platform.startswith("linux"),
    reason="the 190 driver is confined only on macOS (sandbox-exec) and Linux")


@pytest.fixture(scope="module")
def harness():
    sys.path.insert(0, str(REPO / "evals"))
    sys.path.insert(0, str(REPO / "agents"))
    import run_eval as R  # noqa: PLC0415
    return R


def _task(lint: bool) -> dict:
    task = json.loads(TASK.read_text())
    if not lint:
        script = task["validation"]["script"]
        assert script.count(LINT_LINE) == 1, "the validator's lint call moved; update this test"
        task["validation"]["script"] = script.replace(LINT_LINE, "problems = []\n")
    return task


def _run(R, task: dict, sol: str) -> tuple[bool, str]:
    R.run_cleanup(task, log=lambda *_: None)
    assert R.run_setup(task, log=lambda *_: None)
    try:
        dest = task["dest"]
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy(REFS / sol, dest)
        R.run_pre_validate(task, log=lambda *_: None)
        ok, out = R.run_validation(task)
    finally:
        R.run_cleanup(task, log=lambda *_: None)
    out = (out or "").strip()
    if "could not confine" in out:
        pytest.skip(f"no confinement on this host: {out[-120:]}")
    return ok, out


def test_reference_passes_with_lint_stripped(harness):
    ok, out = _run(harness, _task(lint=False), "autograd.py")
    assert ok, out[-300:]


def test_full_power_probe_reaches_nothing(harness):
    ok, out = _run(harness, _task(lint=False), "wrong_cheat_procinfo.py")
    assert not ok, f"the probe PASSED, so it reached the oracle or a compute library: {out[-300:]}"
    assert re.search(r"^import failed: RuntimeError", out, re.M), out[-300:]


def test_full_power_probe_is_rejected_by_the_lint(harness):
    ok, out = _run(harness, _task(lint=True), "wrong_cheat_procinfo.py")
    assert not ok
    assert re.search(r"^forbidden api: line \d+: import of 'glob'", out, re.M), out[-300:]


@pytest.mark.parametrize("sol", [
    "leak.py", "wrong_leak_argv.py", "wrong_cheat_torch_import.py",
    "wrong_cheat_module_walk.py", "wrong_cheat_wrapper_torch.py",
    "wrong_cheat_git_oracle.py", "wrong_cheat_oracle_file.py", "wrong_cheat_fileio.py",
])
def test_cheat_fails_without_the_lint(harness, sol):
    ok, out = _run(harness, _task(lint=False), sol)
    assert not ok, f"{sol} PASSED with the lint stripped: {out[-300:]}"
    assert "forbidden api" not in out
