"""--jobs N parallel eval harness (evals/run_eval.py) tests.

The parallel harness isolates /tmp fixtures by SCHEDULING, not path
rewriting: units are grouped by base task (variants share a fixture dir),
groups run in parallel, units within a group run sequentially. Invariants
pinned here:

  1. Two variants of the same base task NEVER run concurrently.
  2. Distinct base tasks DO overlap in time (the whole point of --jobs).
  3. Parallel results match a sequential run in content and task ORDER.
  4. jobs is clamped to the server's /props total_slots (and to --slots).
  5. The scheduling-isolation precondition holds on the REAL suite: every
     /tmp/eval_* fixture dir belongs to exactly one task file.
  6. v5 arms (--arms): a base group stays serialized ACROSS arms, the arms
     of one unit run back to back, identical requests are shared, and each
     arm gets its own results file with harness.arm / harness.pair_id.
  7. Groups start longest-first by planned seconds (est_s x arms).
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
        self.events: list[tuple] = []          # (unit, arm, "start"|"end")
        self.walls: dict = {}
        self.max_iters: dict = {}

    def run_agent(self, task, base_url, max_iter_override=None, timeout_scale=1.0,
                  env_overlay=None, wall_s=None):
        self.timeout_scales.add(timeout_scale)
        group = task.get("base_id", task["id"])
        arm = (env_overlay or {}).get("OPENBEAST_EVAL_ARM")
        self.walls[task["id"]] = wall_s
        self.max_iters[task["id"]] = max_iter_override
        with self.lock:
            self.events.append((task["id"], arm, "start"))
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
            self.events.append((task["id"], arm, "end"))
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


def test_runtime_provenance_stamps_repo_sha():
    """Every results file must record which OpenBeast harness produced it —
    the agent runner drifts independently of the inference engine."""
    import subprocess
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    run_eval = importlib.import_module("run_eval")
    info = run_eval.capture_runtime_info()
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    assert info.get("openbeast_commit") == head
    assert isinstance(info.get("openbeast_dirty"), bool)


def test_server_flag_parsing():
    """Serve flags are experiment config (the 2026-08-20 rows ran under an
    unrecorded local REASONING_BUDGET) — the parser must recover them."""
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    run_eval = importlib.import_module("run_eval")
    cmd = ("/x/llama-server -m /w/m.gguf -a Alias Name -ngl 99 -c 262144 "
           "-np 6 --kv-unified -ctk q4_0 -ctv q4_0 --reasoning-budget 4096")
    info = run_eval._parse_server_flags(cmd)
    assert info["reasoning_budget"] == "4096"
    assert info["parallel_slots"] == "6"
    assert info["context"] == "262144"
    assert info["kv_cache_type"] == "q4_0"
    assert "reasoning" not in info  # --reasoning flag absent -> key absent
    uncapped = run_eval._parse_server_flags(cmd.replace(" --reasoning-budget 4096", ""))
    assert "reasoning_budget" not in uncapped


def test_server_flag_parsing_last_occurrence_wins():
    """serve.sh appends the global REASONING_BUDGET override after a serve
    script's baked flag; llama-server obeys the LAST one, so must we — or a
    -1/4096 override is stamped rb20480 and replays as the capped era."""
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    run_eval = importlib.import_module("run_eval")
    base = "/x/llama-server -m /w/m.gguf -c 8192 --reasoning-budget 20480 --jinja"
    assert run_eval._parse_server_flags(base)["reasoning_budget"] == "20480"
    info = run_eval._parse_server_flags(base + " -c 4096 --reasoning-budget -1")
    assert info["reasoning_budget"] == "-1"
    assert info["context"] == "4096"
    info = run_eval._parse_server_flags(base + " --reasoning-budget=4096 -np=2")
    assert info["reasoning_budget"] == "4096"
    assert info["parallel_slots"] == "2"


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


# --- v5: arms in one process, longest-first ------------------------------------

def _arm_run(tmp_path, monkeypatch, jobs: int, delay: float = 0.05):
    run_eval = _load_run_eval(tmp_path, server_slots=8)
    # Two treatments that need no toolchain: B turns push-diagnostics on,
    # whose assist scope covers go (not python) — so a python unit's B
    # request is byte-identical to A's and must be SHARED, never re-run.
    monkeypatch.setattr(run_eval, "BUILTIN_ARMS", {"A": {}, "B": {"diag": True}})
    monkeypatch.setattr(run_eval, "BOARD_ARM", "A")
    tracker = _Tracker(delay=delay)
    run_eval.run_agent = tracker.run_agent
    out = run_eval.run_eval(model_name="fake-model", use_cache=False, jobs=jobs,
                            arms=["A", "B"])
    return run_eval, tracker, out


def test_arms_serialize_each_base_group_across_arms(tmp_path, monkeypatch):
    _re, tracker, out = _arm_run(tmp_path, monkeypatch, jobs=4, delay=0.1)
    assert not tracker.group_violation, "two jobs of one base group ran concurrently"
    assert tracker.saw_overlap, "distinct groups should still overlap"
    # The arms of one unit are adjacent within its group's timeline.
    starts = [(u, a) for u, a, ev in tracker.events if ev == "start"]
    for base in ("10_aa", "10_bb", "10_cc"):
        seq = [(u, a) for u, a in starts if u.startswith(base)]
        assert seq == [(f"{base}_python", "A"), (f"{base}_go", "A"), (f"{base}_go", "B")], seq


def test_arms_write_one_file_per_arm_with_pair_id(tmp_path, monkeypatch):
    _re, tracker, out = _arm_run(tmp_path, monkeypatch, jobs=1)
    assert set(out["arms"]) == {"A", "B"}
    pid = out["pair_id"]
    for arm, path in out["paths"].items():
        assert f"-{arm}-" in Path(path).name
        data = json.loads(Path(path).read_text())
        assert data["harness"]["arm"] == arm and data["harness"]["pair_id"] == pid
        assert len(data["tasks"]) == 7
    b = {t["id"]: t for t in out["arms"]["B"]["tasks"]}
    # python units: identical request -> shared, not re-run
    assert b["10_aa_python"]["shared_from"] == "A" and "shared_from" not in b["10_aa_go"]
    assert b["20_solo"]["shared_from"] == "A"
    ran_b = {u for u, a, ev in tracker.events if a == "B" and ev == "start"}
    assert ran_b == {"10_aa_go", "10_bb_go", "10_cc_go"}
    # diag runs only on in-scope units: B's harness says which
    assert out["arms"]["B"]["harness"]["diag_scope"]
    assert out["arms"]["A"]["harness"]["diagnostics"] is False


def test_arm_units_plan_marks_unmeasured_pairs(tmp_path, monkeypatch):
    run_eval = _load_run_eval(tmp_path, server_slots=8)
    monkeypatch.setattr(run_eval, "BUILTIN_ARMS", {"A": {}, "B": {"diag": True}})
    monkeypatch.setattr(run_eval, "BOARD_ARM", "A")
    tracker = _Tracker(delay=0.01)
    run_eval.run_agent = tracker.run_agent
    out = run_eval.run_eval(model_name="fake-model", use_cache=False, jobs=1,
                            arms=["A", "B"], arm_units={"B": {"10_aa_go"}},
                            arm_meta={"B": {"stratified": True}})
    b = {t["id"]: t for t in out["arms"]["B"]["tasks"]}
    assert "reason" not in b["10_aa_go"]
    assert b["10_bb_go"]["reason"] == run_eval.NOT_IN_ARM
    assert out["arms"]["B"]["harness"]["arm_meta"] == {"stratified": True}
    # an unmeasured pair is not an outcome: neither passed nor failed
    s = out["arms"]["B"]["summary"]
    assert s["passed"] + s["failed"] == 7 - s["not_in_arm"]


def test_budget_deadline_skips_instead_of_failing(tmp_path):
    run_eval = _load_run_eval(tmp_path, server_slots=8)
    tracker = _Tracker(delay=0.0)
    run_eval.run_agent = tracker.run_agent
    out = run_eval.run_eval(model_name="fake-model", use_cache=False, jobs=1,
                            deadline=time.time() - 1)
    assert all(t["reason"] == "budget_skip" for t in out["tasks"])
    assert out["summary"]["failed"] == 0 and not tracker.events
    import scoring
    entry = scoring.score_run(out)
    assert entry["tasks_total"] == 0                 # never scored as failures


def test_longest_first_ordering():
    import run_eval as re_
    groups = [["s"], ["l1", "l2"], ["m"]]
    w = {"s": 10, "l1": 100, "l2": 100, "m": 50}
    assert re_.order_groups_longest_first(groups, lambda j: w[j]) == \
        [["l1", "l2"], ["m"], ["s"]]
    # stable on ties: input order kept
    assert re_.order_groups_longest_first([["a"], ["b"]], lambda j: 1) == [["a"], ["b"]]


def test_parallel_run_starts_the_longest_group_first(tmp_path):
    run_eval = _load_run_eval(tmp_path, server_slots=1)
    # 20_solo is declared the longest unit by far
    p = tmp_path / "tasks" / "20_solo.json"
    d = json.loads(p.read_text())
    d["est_s"] = 10_000
    p.write_text(json.dumps(d))
    tracker = _Tracker(delay=0.05)
    run_eval.run_agent = tracker.run_agent
    run_eval._fetch_server_slots = lambda base_url: 2
    run_eval.run_eval(model_name="fake-model", use_cache=False, jobs=2)
    starts = [u for u, _a, ev in tracker.events if ev == "start"]
    # In task-file order 20_solo would be submitted LAST (after 10_cc);
    # longest-first submits it first, so it starts among the first two.
    assert starts.index("20_solo") < 2
    assert starts.index("20_solo") < starts.index("10_cc_python")


def test_unit_wall_cap_replaces_max_iter_minutes(tmp_path):
    import run_eval as re_
    assert re_.agent_wall_s({"max_iter": 40}, 40) == 2400          # v4 rule kept
    assert re_.agent_wall_s({"max_iter": 40, "_max_wall_s": 900}, 40) == 900
    assert re_.agent_wall_s({"max_iter": 40, "max_wall_s": 900}, 40, cap_override=300) == 300
    assert re_.agent_wall_s({"max_iter": 10}, 10, cap_override=3000) == 600


def test_slots_flag_clamps_when_props_is_unreadable(tmp_path, capsys):
    run_eval = _load_run_eval(tmp_path, server_slots=None)
    tracker = _Tracker(delay=0.0)
    run_eval.run_agent = tracker.run_agent
    out = run_eval.run_eval(model_name="fake-model", use_cache=False, jobs=8, slots=3)
    assert out["jobs"] == 3
    assert "--slots exposes 3" in capsys.readouterr().out


def test_wall_scaled_turns_rule():
    import run_eval as re_
    f = re_.wall_scaled_turns
    assert f({"max_iter": 20, "_max_wall_s": 600}, 15) == 40      # medium
    assert f({"max_iter": 25, "_max_wall_s": 900}, 15) == 60      # hard
    assert f({"max_iter": 30, "_max_wall_s": 1500}, 15) == 100    # xl
    assert f({"max_iter": 40, "_max_wall_s": 300}, 15) == 40      # never below max_iter
    assert f({"max_iter": 12}, 15) == 12                          # no wall cap: unchanged
    assert f({"max_iter": 12, "max_wall_s": 600}, 0) == 12        # off


def test_turns_per_wall_s_reaches_the_agent_and_the_result(tmp_path):
    run_eval = _load_run_eval(tmp_path, server_slots=1)
    tracker = _Tracker(delay=0.0)
    run_eval.run_agent = tracker.run_agent
    orig = run_eval.load_tasks
    run_eval.load_tasks = lambda f=None: [dict(t, max_wall_s=600) for t in orig(f)]
    out = run_eval.run_eval(model_name="fake-model", use_cache=False, turns_per_wall_s=15)
    assert tracker.max_iters and set(tracker.max_iters.values()) == {40}
    assert out["turns_per_wall_s"] == 15
    assert all(r["max_iter"] == 40 for r in out["tasks"])
    # An explicit --max-iter still wins over the wall-scaled budget.
    tracker2 = _Tracker(delay=0.0)
    run_eval.run_agent = tracker2.run_agent
    run_eval.run_eval(model_name="fake-model", use_cache=False, turns_per_wall_s=15,
                      max_iter_override=7)
    assert set(tracker2.max_iters.values()) == {7}


def test_every_result_row_names_its_language(tmp_path):
    # A non-variant unit carried no `language`, and evals/ablation.py bucketed
    # it as python (the shell unit 179 in the L2 audit, 2026-11-16).
    run_eval = _load_run_eval(tmp_path, server_slots=1)
    tracker = _Tracker(delay=0.0)
    run_eval.run_agent = tracker.run_agent
    out = run_eval.run_eval(model_name="fake-model", use_cache=False)
    assert out["tasks"] and all(r.get("language") for r in out["tasks"])


def test_wall_scale_doubles_the_wall_but_keeps_the_turns(tmp_path):
    # Arm C (2026-11-16): thinking makes turns slower, so it gets 2x the wall
    # while its turn budget stays arm B's (computed from the base wall).
    run_eval = _load_run_eval(tmp_path, server_slots=1)
    orig = run_eval.load_tasks
    run_eval.load_tasks = lambda f=None: [dict(t, max_wall_s=600) for t in orig(f)]
    base, scaled = _Tracker(delay=0.0), _Tracker(delay=0.0)
    run_eval.run_agent = base.run_agent
    run_eval.run_eval(model_name="fake-model", use_cache=False, turns_per_wall_s=15)
    run_eval.run_agent = scaled.run_agent
    out = run_eval.run_eval(model_name="fake-model", use_cache=False, turns_per_wall_s=15,
                            wall_scale=2.0)
    assert set(base.walls.values()) == {600} and set(scaled.walls.values()) == {1200}
    assert set(base.max_iters.values()) == set(scaled.max_iters.values()) == {40}
    assert out["wall_scale"] == 2.0
