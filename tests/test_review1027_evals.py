"""Review 2026-10-27 eval-harness fixes (R6-3, R6-6, R6-8, R6-9, R6-10, R9-04).

Every case builds its own input: no GPU, no real server, no real fixture
dir, no real lease. Each asserts the negative control too.
"""

import importlib
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
sys.path.insert(0, str(EVALS))
sys.path.insert(0, str(ROOT / "agents"))


def _fresh(mod):
    for m in ("cache", "run_eval", "benchmark_all", "scoring", "profile_mtp"):
        sys.modules.pop(m, None)
    return importlib.import_module(mod)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie is dead for our purposes.
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


# ---------------------------------------------------------------------------
# R6-3: benchmark_all's SIGTERM handler must reap the live agents.
# ---------------------------------------------------------------------------

def test_benchmark_all_sigterm_reaps_live_agents(tmp_path):
    """Drive the real benchmark_all.main() (cache-only: no lease, no server)
    with run_sweep replaced by a fake that starts an 'agent' in its own
    session, registers it the way run_eval does, and blocks. SIGTERM the
    driver: the agent must die with it."""
    marker = tmp_path / "agent.pid"
    driver = tmp_path / "driver.py"
    driver.write_text(textwrap.dedent(f"""
        import os, subprocess, sys, time
        sys.path.insert(0, {str(EVALS)!r})
        sys.path.insert(0, {str(ROOT / 'agents')!r})
        import benchmark_all, run_eval

        def fake_sweep(*a, **k):
            child = subprocess.Popen(
                [sys.executable, "-c",
                 "import os, time; open({str(marker)!r}, 'w').write(str(os.getpid())); time.sleep(120)"],
                start_new_session=True)
            with run_eval._live_pgid_lock:
                run_eval._live_pgids.add(child.pid)
            time.sleep(120)
            return {{}}

        benchmark_all.run_sweep = fake_sweep
        slug = benchmark_all.MODELS[0]["slug"]
        sys.argv = ["benchmark_all.py", "--cache-only", "--no-leaderboard", "--models", slug]
        benchmark_all.main()
    """))
    p = subprocess.Popen([sys.executable, str(driver)], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    agent = None
    try:
        for _ in range(200):
            if marker.exists() and marker.read_text().strip():
                agent = int(marker.read_text())
                break
            time.sleep(0.05)
        assert agent, "fake agent never started"
        assert _alive(agent)                         # negative control
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=20)
        assert p.returncode == 130, p.stdout.read()
        for _ in range(100):
            if not _alive(agent):
                break
            time.sleep(0.05)
        assert not _alive(agent), "agent orphaned by benchmark_all's SIGTERM handler"
    finally:
        if p.poll() is None:
            p.kill()
        if agent and _alive(agent):
            os.kill(agent, signal.SIGKILL)
