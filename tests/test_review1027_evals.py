"""Eval-integrity fixes from the 2026-10-27 review (suite v4.1 era roll).

R6-1  a Python validator must FINISH to pass: a solution that exits the
      validator's process at import time (sys.exit(0), os._exit(0),
      `raise SystemExit`) used to PASS 96 of 99 Python validators.
R6-5  the era hash covers the harness contract (evals/harness.py) and only
      the files an eval unit actually reads.
R6-11 eval transcripts go outside the repo, and the agent's file tools
      refuse earlier transcripts, the cache, task specs and refs.

Every case builds its own fixture under tmp_path.
"""

import json
import os
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


# --------------------------------------------------------------------------
# R6-5: the era covers the harness contract, and only files the eval reads
# --------------------------------------------------------------------------

import cache  # noqa: E402
import harness  # noqa: E402


def test_context_files_are_what_an_eval_unit_reads():
    names = {p.relative_to(ROOT).as_posix() for p in cache.CONTEXT_FILES}
    assert names == {"system-prompt.md", "agents/runner.py", "agents/tools.py",
                     "evals/harness.py", "evals/SUITE_VERSION"}
    for p in cache.CONTEXT_FILES:
        assert p.exists(), p


def test_dropped_files_are_really_unread_by_the_eval_agent():
    # If runner.py or tools.py ever reads these again, they must go back
    # into CONTEXT_FILES.
    for f in ("agents/runner.py", "agents/tools.py"):
        src = (ROOT / f).read_text()
        assert "opencode.json" not in src, f
        assert "system-prompt-tools" not in src, f
    # And the one prompt file that stays is the one the runner loads.
    assert "system-prompt.md" in (ROOT / "agents" / "runner.py").read_text()


def test_harness_change_moves_the_era(tmp_path, monkeypatch):
    h = tmp_path / "harness.py"
    h.write_text((ROOT / "evals" / "harness.py").read_text())
    monkeypatch.setattr(cache, "CONTEXT_FILES",
                        [h if p.name == "harness.py" else p for p in cache.CONTEXT_FILES])
    before = cache.context_hash()
    h.write_text(h.read_text().replace("WALL_SECONDS_PER_ITER = 60",
                                       "WALL_SECONDS_PER_ITER = 61"))
    assert cache.context_hash() != before


def test_run_eval_takes_its_contract_from_harness():
    assert run_eval.VALIDATION_TIMEOUT_S is harness.VALIDATION_TIMEOUT_S
    assert run_eval.task_expected_paths is harness.task_expected_paths
    assert run_eval._PY_VALIDATOR_WRAPPER is harness.PY_VALIDATOR_WRAPPER
    src = (ROOT / "evals" / "run_eval.py").read_text()
    # The literals that used to live here are gone.
    assert '"--workdir", "/tmp"' not in src
    assert "max_iter * 60 * timeout_scale" not in src
    assert 'task.get("max_iter", 15)' not in src


# --------------------------------------------------------------------------
# R6-11: transcripts outside the repo, earlier solutions off limits
# --------------------------------------------------------------------------

def test_default_eval_log_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENBEAST_EVAL_LOG_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert harness.default_eval_log_dir() == str(tmp_path / "state" / "openbeast" / "eval-logs")
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    assert harness.default_eval_log_dir() == str(tmp_path / "h" / ".local" / "state"
                                                 / "openbeast" / "eval-logs")
    monkeypatch.setenv("OPENBEAST_EVAL_LOG_DIR", str(tmp_path / "x"))
    assert harness.default_eval_log_dir() == str(tmp_path / "x")


def test_denied_roots_cover_every_checkout(tmp_path):
    roots = harness.denied_roots(str(ROOT), str(tmp_path / "logs"))
    assert str(tmp_path / "logs") in roots
    assert os.path.realpath(ROOT / "agents" / "logs") in roots
    assert os.path.realpath(ROOT / "evals") in roots
    # A worktree also denies the main checkout's (git worktree list).
    import subprocess
    out = subprocess.run(["git", "-C", str(ROOT), "worktree", "list", "--porcelain"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("worktree "):
            assert os.path.realpath(os.path.join(line[9:], "agents", "logs")) in roots


def test_run_agent_hands_the_runner_log_dir_and_deny_roots(tmp_path, monkeypatch):
    import textwrap
    dump = tmp_path / "dump.json"
    fake = tmp_path / "fake_runner.py"
    fake.write_text(textwrap.dedent(f'''
        import json, os, sys
        json.dump({{"argv": sys.argv[1:],
                   "deny": os.environ.get("OPENBEAST_EVAL_DENY_ROOTS"),
                   "eval": os.environ.get("OPENBEAST_EVAL")}}, open({str(dump)!r}, "w"))
    '''))
    monkeypatch.setattr(run_eval, "RUNNER_PATH", str(fake))
    monkeypatch.setenv("OPENBEAST_EVAL_LOG_DIR", str(tmp_path / "evlogs"))
    run_eval._DENIED_ROOTS.clear()
    run_eval.run_agent({"task": "do it", "max_iter": 1}, "http://127.0.0.1:9/v1")
    got = json.loads(dump.read_text())
    argv = got["argv"]
    assert argv[argv.index("--log-dir") + 1] == str(tmp_path / "evlogs")
    assert argv[argv.index("--workdir") + 1] == "/tmp"
    assert argv[-1] == "do it"
    deny = json.loads(got["deny"])
    assert str(tmp_path / "evlogs") in deny
    assert os.path.realpath(ROOT / "agents" / "logs") in deny
    assert got["eval"] == "1"
