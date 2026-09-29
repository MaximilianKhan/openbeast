"""A wall-timeout kill must take the agent's in-flight TOOL command with it.

tools.run_reaped starts each bash-tool child in its own session, so killing
only the runner's process group left that child running (review finding
eval-harness-5): it kept writing /tmp/eval_* fixtures while the next
variant of the same base task ran. This builds its own fake runner that
spawns exactly that shape of child; nothing touches a real agent or server.
"""

import importlib
import os
import signal
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))


FAKE_RUNNER = textwrap.dedent('''
    import os, subprocess, sys, time
    marker, pidfile = os.environ["T_MARKER"], os.environ["T_PIDFILE"]
    # Same shape as tools.run_reaped: a shell child in its OWN session.
    child = subprocess.Popen(["sh", "-c", f"sleep 2; touch {marker}"],
                             start_new_session=True)
    with open(pidfile, "w") as f:
        f.write(str(child.pid))
    time.sleep(30)   # the "hung" runner; the harness times it out
''')


def _load(tmp_path: Path):
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    run_eval = importlib.import_module("run_eval")
    runner = tmp_path / "fake_runner.py"
    runner.write_text(FAKE_RUNNER)
    run_eval.RUNNER_PATH = str(runner)
    return run_eval


def _wait_for(path: Path, secs: float) -> bool:
    end = time.time() + secs
    while time.time() < end:
        if path.exists() and path.read_text().strip():
            return True
        time.sleep(0.05)
    return False


def _cleanup(pidfile: Path) -> None:
    """Never leave an orphan behind, whichever way the test went."""
    try:
        os.killpg(int(pidfile.read_text()), signal.SIGKILL)
    except (OSError, ValueError):
        pass


def test_timeout_kills_tool_child_in_its_own_session(tmp_path, monkeypatch):
    run_eval = _load(tmp_path)
    marker, pidfile = tmp_path / "marker", tmp_path / "child.pid"
    monkeypatch.setenv("T_MARKER", str(marker))
    monkeypatch.setenv("T_PIDFILE", str(pidfile))
    try:
        # max_iter 1 x 60 s x (1.5/60) -> a 1 s wall budget.
        res = run_eval.run_agent({"task": "t", "max_iter": 1}, "http://127.0.0.1:9/v1",
                                 timeout_scale=1.5 / 60)
        assert res["exit_code"] == -1
        assert _wait_for(pidfile, 2)
        time.sleep(2.5)                    # past the child's `sleep 2`
        assert not marker.exists(), "tool child outlived the timeout kill"
    finally:
        _cleanup(pidfile)


def test_kill_agent_tree_negative_control(tmp_path, monkeypatch):
    """Killing only the runner's group (the old behaviour) leaves the child
    alive — proves the fixture really builds the escaping shape."""
    import subprocess
    run_eval = _load(tmp_path)
    marker, pidfile = tmp_path / "marker", tmp_path / "child.pid"
    env = dict(os.environ, T_MARKER=str(marker), T_PIDFILE=str(pidfile))
    proc = subprocess.Popen([sys.executable, run_eval.RUNNER_PATH], env=env,
                            start_new_session=True)
    try:
        assert _wait_for(pidfile, 5)
        child = int(pidfile.read_text())
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        os.kill(child, 0)                  # still alive: raises if it were gone
    finally:
        _cleanup(pidfile)
        try:
            proc.kill()
        except OSError:
            pass
