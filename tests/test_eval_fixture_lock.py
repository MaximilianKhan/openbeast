"""One harness per /tmp/eval_* fixture root (run_eval.fixture_lock).

Review 1116: a prover run during a live sweep rm -rf'd the sweep's fixture
dirs under its agents (and the sweep its), and both sides were scored on
missing or foreign files without an error. Every harness now holds an
exclusive flock per fixture root for a whole unit.
"""
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))

import run_eval


def test_fixture_lock_refuses_a_second_process_and_is_reentrant(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_FIXTURE_LOCK_DIR", str(tmp_path / "locks"))
    task = {"setup": "mkdir -p /tmp/eval_locktest_1116 /tmp/eval_locktest_1116b",
            "validation": {"script": "ls /tmp/eval_locktest_1116"}}
    assert run_eval.fixture_roots(task) == ["/tmp/eval_locktest_1116", "/tmp/eval_locktest_1116b"]
    holder = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(ROOT / 'evals')!r}); sys.path.insert(0, {str(ROOT / 'agents')!r})
        import run_eval
        with run_eval.fixture_lock({task!r}, wait=False):
            print("held", flush=True)
            time.sleep(30)
    """)
    p = subprocess.Popen([sys.executable, "-c", holder], stdout=subprocess.PIPE, text=True,
                         env=dict(os.environ))
    try:
        assert p.stdout.readline().strip() == "held"
        with pytest.raises(run_eval.FixtureBusy, match=f"pid {p.pid}"):  # noqa: SIM117
            with run_eval.fixture_lock(task, wait=False):
                pass
        assert not run_eval._FIXTURE_LOCKS.get("/tmp/eval_locktest_1116", [None, 0])[1]
    finally:
        p.kill()
        p.wait()
    # the kernel dropped the dead holder's lock; re-entrant within a thread
    with run_eval.fixture_lock(task, wait=False):  # noqa: SIM117  (re-entry is the point)
        with run_eval.fixture_lock(task, wait=False), \
                run_eval.fixture_lock({"setup": "x /tmp/eval_locktest_1116"}, wait=False):
            pass
    assert run_eval._FIXTURE_LOCKS["/tmp/eval_locktest_1116"][1] == 0


def test_fixture_lock_serializes_threads_of_one_process(tmp_path, monkeypatch):
    import threading
    monkeypatch.setenv("OPENBEAST_FIXTURE_LOCK_DIR", str(tmp_path / "locks"))
    task = {"setup": "mkdir -p /tmp/eval_locktest_1116c"}
    inside, overlap = [0], [False]

    def unit():
        with run_eval.fixture_lock(task, wait=True, log=lambda *_: None):
            inside[0] += 1
            overlap[0] |= inside[0] > 1
            time.sleep(0.05)
            inside[0] -= 1

    ts = [threading.Thread(target=unit) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not overlap[0]
