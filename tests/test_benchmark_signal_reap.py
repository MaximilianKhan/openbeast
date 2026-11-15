"""A SIGTERM to a benchmark_all sweep must take its in-flight agents with it.

benchmark_all drives run_eval IN-PROCESS, so run_eval.main()'s signal reaper
is never installed on the campaign path, and every agent runs in its own
session (start_new_session=True) out of reach of a signal to the sweep's
group. The old handler stopped llama-server and sys.exit()ed, orphaning the
runner (review finding campaign-evals: benchmark_all signal orphans agents).
Nothing here touches a real agent, server or GPU lease.
"""

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FAKE_RUNNER = textwrap.dedent('''
    import os, sys, time
    with open(os.environ["T_PIDFILE"], "w") as f:
        f.write(str(os.getpid()))
    time.sleep(60)
''')

DRIVER = textwrap.dedent('''
    import sys
    sys.path.insert(0, sys.argv[1] + "/evals")
    sys.path.insert(0, sys.argv[1] + "/agents")
    import benchmark_all, run_eval
    run_eval.RUNNER_PATH = sys.argv[2]
    benchmark_all._install_signal_handler()
    run_eval.run_agent({"task": "t", "max_iter": 1}, "http://127.0.0.1:9/v1")
''')


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); on Linux /proc tells them apart.
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def test_sigterm_reaps_the_inflight_agent(tmp_path):
    runner, driver = tmp_path / "runner.py", tmp_path / "driver.py"
    pidfile = tmp_path / "agent.pid"
    runner.write_text(FAKE_RUNNER)
    driver.write_text(DRIVER)
    env = dict(os.environ, T_PIDFILE=str(pidfile))
    proc = subprocess.Popen([sys.executable, str(driver), str(ROOT), str(runner)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    agent = None
    try:
        end = time.time() + 15
        while time.time() < end and not (pidfile.exists() and pidfile.read_text()):
            time.sleep(0.05)
        assert pidfile.exists(), "fake agent never started"
        agent = int(pidfile.read_text())
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=15)
        end = time.time() + 5
        while time.time() < end and _alive(agent):
            time.sleep(0.05)
        assert not _alive(agent), "the agent outlived the sweep's SIGTERM"
        assert proc.returncode == 128 + signal.SIGTERM
    finally:
        if proc.poll() is None:
            proc.kill()
        if agent:
            try:
                os.killpg(agent, signal.SIGKILL)
            except OSError:
                pass
