"""--jobs N parallel eval harness (evals/run_eval.py) tests.

The parallel harness isolates /tmp fixtures by SCHEDULING, not path
rewriting: units are grouped by base task (variants share a fixture dir),
groups run in parallel, units within a group run sequentially. Invariants
pinned here:

  1. Two variants of the same base task NEVER run concurrently.
  2. Distinct base tasks DO overlap in time (the whole point of --jobs).
  3. Parallel results match a sequential run in content and task ORDER.
  4. jobs is clamped to the server's /props total_slots.
  5. The scheduling-isolation precondition holds on the REAL suite: every
     /tmp/eval_* fixture dir belongs to exactly one task file.
"""

import importlib
import json
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))


def _make_suite(tasks_dir: Path) -> None:
    """3 base tasks x 2 language variants + 1 singleton = 7 units."""
    for base in ("aa", "bb", "cc"):
        (tasks_dir / f"10_{base}.json").write_text(json.dumps({
            "id": f"10_{base}", "name": f"task {base}", "difficulty": "easy",
            "task": f"do {base}",
            "validation": {"type": "bash", "script": "true"},
            "max_iter": 1,
            "variants": [
                {"id": "python", "language": "python"},
                {"id": "go", "language": "go"},
            ],
        }))
    (tasks_dir / "20_solo.json").write_text(json.dumps({
        "id": "20_solo", "name": "solo", "difficulty": "easy",
        "task": "do solo",
        "validation": {"type": "bash", "script": "true"},
        "max_iter": 1,
    }))


def _load_run_eval(tmp_path: Path, server_slots: int):
    """Fresh run_eval module wired to a temp suite, with the network probe
    stubbed out."""
    tasks_dir = tmp_path / "tasks"
    results_dir = tmp_path / "results"
    tasks_dir.mkdir(parents=True)
    results_dir.mkdir(parents=True)
    _make_suite(tasks_dir)
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    importlib.import_module("cache")
    run_eval = importlib.import_module("run_eval")
    run_eval.TASKS_DIR = str(tasks_dir)
    run_eval.RESULTS_DIR = str(results_dir)
    run_eval._fetch_server_slots = lambda base_url: server_slots
    return run_eval


class _Tracker:
    """Concurrency observer installed as the fake agent."""

    def __init__(self, delay: float):
        self.delay = delay
        self.lock = threading.Lock()
        self.active_per_group: dict[str, int] = {}
        self.current = 0
        self.max_concurrent = 0
        self.group_violation = False
        self.saw_overlap = False
        self.timeout_scales: set = set()

    def run_agent(self, task, base_url, max_iter_override=None, timeout_scale=1.0):
        self.timeout_scales.add(timeout_scale)
        group = task.get("base_id", task["id"])
        with self.lock:
            self.active_per_group[group] = self.active_per_group.get(group, 0) + 1
            if self.active_per_group[group] > 1:
                self.group_violation = True
            self.current += 1
            self.max_concurrent = max(self.max_concurrent, self.current)
            if self.current > 1:
                self.saw_overlap = True
        time.sleep(self.delay)
        with self.lock:
            self.active_per_group[group] -= 1
            self.current -= 1
        return {
            "exit_code": 0, "elapsed_seconds": self.delay,
            "stdout": "", "stderr": "",
            "tokens": {"prompt": 1, "completion": 1, "total": 2},
        }


def _run(run_eval, jobs: int, tracker: _Tracker):
    run_eval.run_agent = tracker.run_agent
    return run_eval.run_eval(model_name="fake-model", use_cache=False, jobs=jobs)


def test_parallel_respects_fixture_groups(tmp_path):
    run_eval = _load_run_eval(tmp_path, server_slots=8)
    tracker = _Tracker(delay=0.15)
    results = _run(run_eval, jobs=4, tracker=tracker)

    assert not tracker.group_violation, \
        "two variants of the same base task ran concurrently (shared /tmp fixtures)"
    assert tracker.saw_overlap, \
        "no two units ever overlapped — the pool is not actually parallel"
    assert tracker.max_concurrent <= 4
    assert results["summary"]["passed"] == 7
    assert results["jobs"] == 4
    # Contention-scaled per-iteration budget: jobs=4 -> 2.0x (linear ramp).
    assert tracker.timeout_scales == {2.0}


def test_sequential_keeps_unscaled_timeout(tmp_path):
    tracker = _Tracker(delay=0.01)
    _run(_load_run_eval(tmp_path, server_slots=8), jobs=1, tracker=tracker)
    assert tracker.timeout_scales == {1.0}


def test_parallel_matches_sequential_order(tmp_path):
    seq = _run(_load_run_eval(tmp_path / "seq", server_slots=8),
               jobs=1, tracker=_Tracker(delay=0.01))
    par = _run(_load_run_eval(tmp_path / "par", server_slots=8),
               jobs=4, tracker=_Tracker(delay=0.05))

    seq_ids = [t["id"] for t in seq["tasks"]]
    par_ids = [t["id"] for t in par["tasks"]]
    assert par_ids == seq_ids, "parallel run changed the recorded task order"
    assert par["summary"] == seq["summary"]
    for s, p in zip(seq["tasks"], par["tasks"]):
        assert s["passed"] == p["passed"]
        assert s.get("language") == p.get("language")


def test_jobs_clamped_to_server_slots(tmp_path):
    run_eval = _load_run_eval(tmp_path, server_slots=2)
    tracker = _Tracker(delay=0.1)
    results = _run(run_eval, jobs=8, tracker=tracker)

    assert tracker.max_concurrent <= 2, \
        "--jobs was not clamped to the server's total_slots"
    assert results["jobs"] == 2
    assert results["summary"]["passed"] == 7


def test_fixture_dirs_unique_per_task_file():
    """The precondition scheduling-isolation rests on: no /tmp/eval_* dir is
    referenced by more than one task file. A new task reusing another task's
    fixture dir would silently break --jobs > 1."""
    owners: dict[str, set[str]] = {}
    for path in (ROOT / "evals" / "tasks").glob("*.json"):
        for tok in set(re.findall(r"/tmp/eval[A-Za-z0-9_]*", path.read_text())):
            owners.setdefault(tok, set()).add(path.name)
    shared = {tok: sorted(files) for tok, files in owners.items() if len(files) > 1}
    assert not shared, f"fixture dirs shared across task files: {shared}"
