"""Stopping a runner mid-tool-call must stop the tool command too.

Review chat-sessions-orphaned-tool-children-on-stop (round 2): every
bash-tool command runs in its own session (tools.run_reaped), so SIGTERM to
the runner (job.sh stop, MCP stop_agent, the console's escalation) killed the
runner and orphaned the command, with nothing left to enforce its timeout.

Each case launches runner.main() in a child interpreter whose run_agent is
stubbed to make exactly one run_reaped call (no model, no server), waits for
the tool command to record its pid, signals the runner, and checks the tool
command is gone. The negative control proves the probe detects a survivor —
and then kills it itself, so no test leaves an orphan behind.

Run: python3 -m pytest tests/test_runner_stop.py -q
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="procfs process checks are Linux-only")

_PROBE = r'''
import sys
sys.path.insert(0, sys.argv[1])
import runner, tools
mode, pidfile, cmd = sys.argv[2], sys.argv[3], sys.argv[4]
def fake_run_agent(**kw):
    tools.run_reaped(cmd.replace("PIDFILE", pidfile), 120)
runner.run_agent = fake_run_agent
if mode == "no-handler":
    runner.install_stop_handlers = lambda: None
sys.argv = ["runner.py", "stub task"]
runner.main()
'''

_QUIET = "echo $$ > PIDFILE; exec sleep 97.31"
# `trap '' TERM` is inherited by the sleeps: only the SIGKILL escalation ends it.
_STUBBORN = "trap '' TERM; echo $$ > PIDFILE; while :; do sleep 0.2; done"


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def _launch(tmp_path, mode, cmd):
    pidfile = tmp_path / "tool.pid"
    env = {k: v for k, v in os.environ.items()
           if k not in ("OPENBEAST_KEEP_DUMPABLE",)}
    proc = subprocess.Popen(
        [sys.executable, "-c", _PROBE, str(ROOT / "agents"), mode,
         str(pidfile), cmd],
        env=env, cwd=str(tmp_path), start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if pidfile.exists() and pidfile.read_text().strip():
            return proc, int(pidfile.read_text())
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    proc.kill()
    raise AssertionError(f"tool never started: {proc.stderr.read()!r}")


def _cleanup(tool_pid):
    # Only ever a pid this test's tool command recorded, and only while it is
    # still that command's group.
    if _alive(tool_pid):
        try:
            os.killpg(tool_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _gone_within(pid, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_stop_signal_kills_tool_command(tmp_path, sig):
    runner, tool_pid = _launch(tmp_path, "handler", _QUIET)
    try:
        assert os.getpgid(tool_pid) == tool_pid        # its own group
        assert os.getpgid(tool_pid) != os.getpgid(runner.pid)
        os.killpg(runner.pid, sig)                     # what job.sh/console do
        rc = runner.wait(timeout=20)
        assert rc == -sig, runner.stderr.read()        # still dies OF the signal
        assert _gone_within(tool_pid, 5)
    finally:
        _cleanup(tool_pid)


def test_stop_escalates_to_sigkill_for_a_term_ignoring_command(tmp_path):
    runner, tool_pid = _launch(tmp_path, "handler", _STUBBORN)
    try:
        t0 = time.monotonic()
        os.kill(runner.pid, signal.SIGTERM)
        assert runner.wait(timeout=20) == -signal.SIGTERM
        assert _gone_within(tool_pid, 5)
        assert time.monotonic() - t0 < 15
    finally:
        _cleanup(tool_pid)


def test_sigint_kills_tool_command_and_raises_keyboardinterrupt(tmp_path):
    runner, tool_pid = _launch(tmp_path, "handler", _QUIET)
    try:
        os.kill(runner.pid, signal.SIGINT)
        runner.wait(timeout=20)
        assert "KeyboardInterrupt" in runner.stderr.read().decode()
        assert _gone_within(tool_pid, 5)
    finally:
        _cleanup(tool_pid)


def test_without_handler_the_tool_command_is_orphaned(tmp_path):
    # Negative control: the pre-fix behaviour, reproduced — proves the probe
    # can see a survivor on this kernel.
    runner, tool_pid = _launch(tmp_path, "no-handler", _QUIET)
    try:
        os.kill(runner.pid, signal.SIGTERM)
        assert runner.wait(timeout=20) == -signal.SIGTERM
        time.sleep(0.5)
        assert _alive(tool_pid)
    finally:
        _cleanup(tool_pid)
    assert _gone_within(tool_pid, 5)


def test_registry_is_empty_after_calls(tmp_path):
    sys.path.insert(0, str(ROOT / "agents"))
    import tools
    tools.run_reaped("true", 10)
    with pytest.raises(subprocess.TimeoutExpired):
        tools.run_reaped("sleep 5", 0.3)
    assert tools._LIVE_GROUPS == {}
    assert tools.kill_live_children(0) == 0
