"""Eval-integrity fixes from the 2026-10-27 review (suite v4.1 era roll).

R6-1  a Python validator must FINISH to pass: a solution that exits the
      validator's process at import time (sys.exit(0), os._exit(0),
      `raise SystemExit`) used to PASS 96 of 99 Python validators.

Every case builds its own fixture under tmp_path.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))

import run_eval  # noqa: E402



# --------------------------------------------------------------------------
# R6-1: the completion sentinel
# --------------------------------------------------------------------------

def _validator_for(path: Path) -> str:
    return (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('m', {str(path)!r})\n"
        "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
        "assert mod.add(2, 3) == 5, 'add is wrong'\n"
        "print('OK')\n"
    )


def _validate(tmp_path, solution: str, script: str | None = None):
    sol = tmp_path / "sol.py"
    sol.write_text(solution)
    task = {"validation": {"type": "python",
                           "script": script or _validator_for(sol)}}
    return run_eval.run_validation(task)


def test_correct_solution_passes_and_sentinel_is_stripped(tmp_path):
    ok, out = _validate(tmp_path, "def add(a, b):\n    return a + b\n")
    assert ok, out
    assert out == "OK"                       # no sentinel / nonce left behind
    assert run_eval.VALIDATOR_SENTINEL not in out


def test_wrong_solution_still_fails(tmp_path):
    ok, out = _validate(tmp_path, "def add(a, b):\n    return a - b\n")
    assert not ok and "add is wrong" in out


@pytest.mark.parametrize("cheat", [
    "import sys\nsys.exit(0)\n",
    "import sys\nsys.exit()\n",
    "import os\nos._exit(0)\n",
    "raise SystemExit\n",
    "import sys\ndef main():\n    return 0\nsys.exit(main())\n",
])
def test_exiting_at_import_fails(tmp_path, cheat):
    ok, out = _validate(tmp_path, cheat)
    assert not ok, out


def test_reading_own_cmdline_and_environ_cannot_forge_the_sentinel(tmp_path):
    # The nonce reaches the wrapper on stdin only: what /proc/self shows is
    # not enough to print the completion line.
    cheat = (
        "import os, sys\n"
        "blob = open('/proc/self/cmdline','rb').read() + open('/proc/self/environ','rb').read()\n"
        f"pfx = {run_eval.VALIDATOR_SENTINEL!r}\n"
        "for tok in blob.replace(b'\\0', b' ').split():\n"
        "    print(pfx + tok.decode(errors='replace'))\n"
        "sys.stdout.flush(); os._exit(0)\n"
    )
    ok, out = _validate(tmp_path, cheat)
    assert not ok


def test_validator_raising_systemexit_with_message_keeps_its_meaning(tmp_path):
    script = "raise SystemExit('no ValueError on length mismatch')\n"
    ok, out = _validate(tmp_path, "", script=script)
    assert not ok and "no ValueError on length mismatch" in out


def test_validator_that_exits_zero_early_is_reported(tmp_path):
    ok, out = _validate(tmp_path, "", script="import sys\nsys.exit(0)\n")
    assert not ok
    assert run_eval.VALIDATOR_UNFINISHED in out


def test_validator_globals_are_main(tmp_path):
    # Validators that use `if __name__ == '__main__'` or pickle by
    # reference to __main__ behave as under a plain `python -c`.
    script = ("import sys\n"
              "def f(): return 1\n"
              "assert __name__ == '__main__'\n"
              "assert sys.modules['__main__'].f is f\n"
              "print('OK')\n")
    ok, out = _validate(tmp_path, "", script=script)
    assert ok, out


def test_bash_validators_unchanged(tmp_path):
    ok, out = run_eval.run_validation(
        {"validation": {"type": "bash", "script": "echo hi; exit 0"}})
    assert ok and out == "hi"
