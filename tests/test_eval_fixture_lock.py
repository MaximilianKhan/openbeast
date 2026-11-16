"""Two harness processes on one host must not share a /tmp/eval_* fixture
root at the same time (review 1116).

Within one run_eval the units of a base share their fixture dir one after
another, but nothing serialised two processes: prove_task beside a
campaign, or two provers of one family, wiped each other's files
mid-validation, and the "missing biquad.py" FAILs looked like model
failures and were banked. run_eval.fixture_lock now holds an flock per
fixture root for each unit's whole lifecycle, in run_eval and in
prove_task's arms. No model, no server.
"""
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))

import run_eval  # noqa: E402


@pytest.fixture(autouse=True)
def _private_lock_dir(tmp_path, monkeypatch):
    # every test (and the holder processes it starts) locks in its own dir
    monkeypatch.setenv("OPENBEAST_FIXTURE_LOCK_DIR", str(tmp_path / "locks"))


TASK = {"id": "lk_a", "setup": "mkdir -p /tmp/eval_lk_test_a && mkdir -p /tmp/eval_lk_test_b/x",
        "validation": {"type": "bash", "script": "test -f /tmp/eval_lk_test_a/s.py"}}

HOLDER = textwrap.dedent('''
    import sys, time
    sys.path.insert(0, {evals!r}); sys.path.insert(0, {agents!r})
    import run_eval
    with run_eval.fixture_lock({task!r}):
        print("held", flush=True)
        time.sleep(1.5)
''')


def test_fixture_roots_are_the_first_component_under_tmp():
    assert run_eval.fixture_roots(TASK) == ["/tmp/eval_lk_test_a", "/tmp/eval_lk_test_b"]
    assert run_eval.fixture_roots({"id": "none", "setup": "true"}) == []


def test_a_second_process_waits_for_the_fixture_root():
    src = HOLDER.format(evals=str(ROOT / "evals"), agents=str(ROOT / "agents"), task=TASK)
    holder = subprocess.Popen([sys.executable, "-c", src], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        said = []
        t0 = time.monotonic()
        with run_eval.fixture_lock(TASK, log=said.append):
            waited = time.monotonic() - t0
        assert waited >= 0.8, waited
        assert any("Waiting for fixture /tmp/eval_lk_test_a" in s for s in said), said
    finally:
        holder.kill()
        holder.wait()


def test_the_lock_dies_with_its_holder():
    src = HOLDER.format(evals=str(ROOT / "evals"), agents=str(ROOT / "agents"), task=TASK)
    holder = subprocess.Popen([sys.executable, "-c", src], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    holder.kill()
    holder.wait()
    t0 = time.monotonic()
    with run_eval.fixture_lock(TASK, log=lambda *_: None):
        pass
    assert time.monotonic() - t0 < 0.5


def test_threads_of_one_process_are_serialised_too():
    """Two units of different bases naming one root, under --jobs."""
    order = []
    entered = threading.Event()

    def first():
        with run_eval.fixture_lock(TASK, log=lambda *_: None):
            entered.set()
            time.sleep(0.5)
            order.append("first-out")

    th = threading.Thread(target=first)
    th.start()
    entered.wait(5)
    with run_eval.fixture_lock({"id": "lk_b", "setup": "mkdir -p /tmp/eval_lk_test_a"},
                               log=lambda *_: None):
        order.append("second-in")
    th.join()
    assert order == ["first-out", "second-in"]


def test_an_unsafe_lock_dir_degrades_with_a_warning(monkeypatch):
    def bad():
        raise OSError("/x/fixture-locks is not a private dir of ours")
    monkeypatch.setattr(run_eval, "_fixture_lock_dir", bad)
    said = []
    with run_eval.fixture_lock(TASK, log=said.append):
        pass
    assert said and "WARNING: no fixture lock" in said[0]


def test_lock_dir_is_private():
    d = run_eval._fixture_lock_dir()
    st = os.lstat(d)
    assert st.st_uid == os.getuid() and not (st.st_mode & 0o077)


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
