"""21_race_condition's validator, both ways (review 2026-10-09, evals F5).

The v4 validator ran 10 threads x 2000 increments and checked the total.
CPython switches threads rarely, so an unsynchronised counter passed by
luck: with the fixture's time.sleep(0) deleted and no lock, 20 of 20.

Each case runs the REAL spec (setup + validation, /tmp/eval_race redirected
into tmp_path) against one edit of the fixture, several times over: the
point of the fix is that the verdict no longer depends on scheduling.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TASK = json.loads((ROOT / "evals" / "tasks" / "21_race_condition.json").read_text())

BODY = "        v = self.value\n        time.sleep(0)\n        self.value = v + 1\n"
INIT = "        self.value = 0\n"
LOCK_INIT = INIT + "        self._lock = threading.Lock()\n"

# name -> (verdict, edit of the fixture source)
SOLUTIONS = {
    # --- no synchronisation at all (the review's three)
    "untouched fixture": (False, lambda s: s),
    "time.sleep(0) deleted, no lock": (False, lambda s: s.replace("        time.sleep(0)\n", "")),
    "self.value += 1, no lock": (False, lambda s: s.replace(BODY, "        self.value += 1\n")),
    # --- locks that do not protect the read-modify-write
    "lock around the read only": (False, lambda s: s.replace(INIT, LOCK_INIT).replace(
        BODY, "        with self._lock:\n            v = self.value\n        self.value = v + 1\n")),
    "a new lock per call": (False, lambda s: s.replace(
        BODY, "        lock = threading.Lock()\n        with lock:\n            self.value += 1\n")),
    "lock in run() only, Counter left racy": (False, lambda s: s.replace(
        "class Counter:\n", "_L = threading.Lock()\n\nclass Counter:\n").replace(
        "        counter.increment()\n", "        with _L:\n            counter.increment()\n")),
    # --- correct
    "Lock around the fixture's own body": (True, lambda s: s.replace(INIT, LOCK_INIT).replace(
        BODY, "        with self._lock:\n            v = self.value\n            time.sleep(0)\n"
              "            self.value = v + 1\n")),
    "Lock around +=": (True, lambda s: s.replace(INIT, LOCK_INIT).replace(
        BODY, "        with self._lock:\n            self.value += 1\n")),
    "RLock with acquire/release": (True, lambda s: s.replace(
        INIT, INIT + "        self._lock = threading.RLock()\n").replace(
        BODY, "        self._lock.acquire()\n        try:\n            self.value = self.value + 1\n"
              "        finally:\n            self._lock.release()\n")),
    "module-level lock": (True, lambda s: s.replace(
        "class Counter:\n", "_L = threading.Lock()\n\nclass Counter:\n").replace(
        BODY, "        with _L:\n            v = self.value\n            self.value = v + 1\n")),
    "value as a locked property": (True, lambda s: s.replace(
        INIT, "        self._value = 0\n        self._lock = threading.Lock()\n\n"
              "    @property\n    def value(self):\n        with self._lock:\n            return self._value\n\n"
              "    @value.setter\n    def value(self, v):\n        with self._lock:\n            self._value = v\n"
        ).replace(BODY, "        with self._lock:\n            self._value += 1\n")),
}
REPEATS = 5


def _box(text, root):
    return text.replace("/tmp/eval", f"{root}/eval")


@pytest.mark.parametrize("name", SOLUTIONS)
def test_validator_verdict_does_not_depend_on_scheduling(name, tmp_path):
    want, edit = SOLUTIONS[name]
    subprocess.run(["bash", "-c", _box(TASK["setup"], tmp_path)], check=True)
    src_path = tmp_path / "eval_race" / "counter.py"
    fixture = src_path.read_text()
    edited = edit(fixture)
    assert name == "untouched fixture" or edited != fixture, "the fixture changed under this test"
    src_path.write_text(edited)
    slowest = 0.0
    for attempt in range(REPEATS):
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, "-c", _box(TASK["validation"]["script"], tmp_path)],
                           capture_output=True, text=True, timeout=30)
        slowest = max(slowest, time.monotonic() - t0)
        assert (r.returncode == 0) is want, (
            f"{name}, attempt {attempt + 1}/{REPEATS}: {(r.stdout + r.stderr)[-300:]}")
    assert slowest < 15, f"validator took {slowest:.1f}s of its 30 s budget"
