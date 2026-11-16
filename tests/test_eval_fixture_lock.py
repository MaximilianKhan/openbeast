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

import importlib
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))

run_eval = importlib.import_module("run_eval")

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
        with run_eval.fixture_lock(TASK, notify=said.append):
            waited = time.monotonic() - t0
        assert waited >= 0.8, waited
        assert any("waiting for /tmp/eval_lk_test_a" in s for s in said), said
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
    with run_eval.fixture_lock(TASK, notify=lambda *_: None):
        pass
    assert time.monotonic() - t0 < 0.5


def test_threads_of_one_process_are_serialised_too():
    """Two units of different bases naming one root, under --jobs."""
    order = []
    entered = threading.Event()

    def first():
        with run_eval.fixture_lock(TASK, notify=lambda *_: None):
            entered.set()
            time.sleep(0.5)
            order.append("first-out")

    th = threading.Thread(target=first)
    th.start()
    entered.wait(5)
    with run_eval.fixture_lock({"id": "lk_b", "setup": "mkdir -p /tmp/eval_lk_test_a"},
                               notify=lambda *_: None):
        order.append("second-in")
    th.join()
    assert order == ["first-out", "second-in"]


def test_an_unsafe_lock_dir_degrades_with_a_warning(monkeypatch):
    def bad():
        raise OSError("/tmp/.ob-eval-locks-x is not a private dir of ours")
    monkeypatch.setattr(run_eval, "_fixture_lock_dir", bad)
    said = []
    with run_eval.fixture_lock(TASK, notify=said.append):
        pass
    assert said and "WARNING: no fixture lock" in said[0]


def test_lock_dir_is_private():
    d = run_eval._fixture_lock_dir()
    st = os.lstat(d)
    assert st.st_uid == os.getuid() and not (st.st_mode & 0o077)
