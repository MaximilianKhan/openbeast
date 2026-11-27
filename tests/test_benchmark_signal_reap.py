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

import pytest

ROOT = Path(__file__).resolve().parent.parent

FAKE_RUNNER = textwrap.dedent('''
    import os, sys, time
    with open(os.environ["T_PIDFILE"], "w") as f:
        f.write(str(os.getpid()))
    # Every agent ever spawned, for the --jobs test.
    with open(os.environ["T_PIDFILE"] + ".all", "a") as f:
        f.write(f"{os.getpid()}\\n")
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


# --jobs N: units run in worker threads that keep going while the handler
# blocks in stop_llama_server (up to ~15 s). A worker whose agent was just
# reaped must not Popen the next one for os._exit to orphan (verifier
# finding on campaign-evals: signal handler still orphans under --jobs).
WORKER_DRIVER = textwrap.dedent('''
    import sys, threading, time
    sys.path.insert(0, sys.argv[1] + "/evals")
    sys.path.insert(0, sys.argv[1] + "/agents")
    import benchmark_all, run_eval
    run_eval.RUNNER_PATH = sys.argv[2]
    # A slow server stop: the window the workers keep running through.
    benchmark_all.stop_llama_server = lambda: time.sleep(2)
    benchmark_all._install_signal_handler()
    def worker():
        while True:
            run_eval.run_agent({"task": "t", "max_iter": 1}, "http://127.0.0.1:9/v1")
    threading.Thread(target=worker, daemon=True).start()
    while True:
        time.sleep(0.1)
''')


def test_sigterm_under_jobs_spawns_no_agent_to_orphan(tmp_path):
    runner, driver = tmp_path / "runner.py", tmp_path / "driver.py"
    pidfile = tmp_path / "agent.pid"
    runner.write_text(FAKE_RUNNER)
    driver.write_text(WORKER_DRIVER)
    env = dict(os.environ, T_PIDFILE=str(pidfile))
    proc = subprocess.Popen([sys.executable, str(driver), str(ROOT), str(runner)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    allfile = Path(str(pidfile) + ".all")
    pids: list[int] = []
    try:
        end = time.time() + 15
        while time.time() < end and not (allfile.exists() and allfile.read_text()):
            time.sleep(0.05)
        assert allfile.exists(), "fake agent never started"
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
        assert proc.returncode == 128 + signal.SIGTERM
        time.sleep(0.5)          # let a raced child finish writing its pid
        pids = [int(x) for x in allfile.read_text().split()]
        end = time.time() + 5
        while time.time() < end and any(_alive(p) for p in pids):
            time.sleep(0.05)
        alive = [p for p in pids if _alive(p)]
        assert not alive, f"agents outlived the sweep's SIGTERM: {alive} of {pids}"
    finally:
        if proc.poll() is None:
            proc.kill()
        for p in pids:
            try:
                os.killpg(p, signal.SIGKILL)
            except OSError:
                pass


# A spawn still inside Popen when the signal lands: the child exists, but
# its thread has not registered it, so the reap's snapshot missed it and
# os._exit orphaned it (review finding campaign-evals: an in-flight spawn
# escapes the reap). SlowPopen widens that window to seconds: the real
# child is forked, then Popen "takes" 3 s to return.
INFLIGHT_DRIVER = textwrap.dedent('''
    import os, subprocess, sys, threading, time
    sys.path.insert(0, sys.argv[1] + "/evals")
    sys.path.insert(0, sys.argv[1] + "/agents")
    import run_eval
    run_eval.RUNNER_PATH = sys.argv[2]
    real = subprocess.Popen
    class SlowPopen(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            open(os.environ["T_PIDFILE"] + ".inpopen", "w").write(str(self.pid))
            time.sleep(3)
    subprocess.Popen = SlowPopen
    run_eval._install_signal_reaper()
    def spawn():
        run_eval.run_agent({"task": "t", "max_iter": 1}, "http://127.0.0.1:9/v1")
    if sys.argv[3] == "worker":
        threading.Thread(target=spawn, daemon=True).start()
        while True:
            time.sleep(0.05)
    spawn()
''')



@pytest.mark.parametrize("where", ["worker", "main"])
def test_sigterm_mid_popen_reaps_the_child(tmp_path, where):
    runner, driver = tmp_path / "runner.py", tmp_path / "driver.py"
    pidfile = tmp_path / "agent.pid"
    runner.write_text(FAKE_RUNNER)
    driver.write_text(INFLIGHT_DRIVER)
    env = dict(os.environ, T_PIDFILE=str(pidfile))
    proc = subprocess.Popen([sys.executable, str(driver), str(ROOT), str(runner), where],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    marker = Path(str(pidfile) + ".inpopen")
    agent = None
    try:
        end = time.time() + 15
        while time.time() < end and not (marker.exists() and marker.read_text()):
            time.sleep(0.02)
        assert marker.exists(), "the spawn never reached Popen"
        agent = int(marker.read_text())
        proc.send_signal(signal.SIGTERM)          # inside the 3 s Popen
        proc.wait(timeout=20)
        assert proc.returncode == 128 + signal.SIGTERM
        end = time.time() + 5
        while time.time() < end and _alive(agent):
            time.sleep(0.05)
        assert not _alive(agent), f"the mid-Popen agent outlived SIGTERM ({where})"
    finally:
        if proc.poll() is None:
            proc.kill()
        if agent:
            try:
                os.killpg(agent, signal.SIGKILL)
            except OSError:
                pass
