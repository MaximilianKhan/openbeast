#!/usr/bin/env python3
"""A reference attempt at a v5 unit by an agent that is NOT driven through
agents/runner.py (2026-11-16: Claude Opus 5.5 as the frontier reference row).

The measured models run inside run_eval: setup -> runner.py -> pre_validate ->
validate. A reference agent (a Claude Code sub-agent with its own tools)
cannot be launched by run_eval, so this splits the same lifecycle in two:

  reference_attempt.py setup <unit>   cleanup + setup, then print ONLY what the
                                      runner would get as its task (task text,
                                      wall budget). The validator, refs and
                                      hidden cases are never printed.
  reference_attempt.py grade <unit>   pre_validate + validate at the given
                                      --scale (default 2.0: the --jobs 4
                                      contention scale the Spark rows were
                                      judged at), stash the solution, append a
                                      ledger row, clean up.

The solver runs `setup` and works; a SEPARATE step runs `grade`, so the solver
never sees a validator's output (a measured model never does either). Rows go
to --ledger as JSONL: unit, passed, validation_output, the setup->grade wall
time and the unit's wall budget (over_budget marks a pass that took longer
than a measured model would have been allowed).

Comparability: same tasks, fixtures, validators and thresholds; a different
agent scaffold (Claude Code tools, not runner.py + agents/tools.py) and no
iteration cap. Report the row as a reference, never as a leaderboard peer.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "evals"))
sys.path.insert(0, os.path.join(REPO, "agents"))

import run_eval as R  # noqa: E402
import suite_manifest as M  # noqa: E402


def _unit(uid: str, suite: str) -> tuple[dict, float]:
    tasks = {t["id"]: t for t in R.load_tasks(None)}
    man = M.load_manifest(suite, tasks=list(tasks.values()), strict=False)
    rid = man["resolved"].get(uid, uid)
    task = tasks.get(rid)
    if task is None:
        sys.exit(f"no such unit: {uid}")
    unit = next((u for u in man["units"] if u["id"] == uid or man["resolved"].get(u["id"]) == rid), {})
    wall = unit.get("max_wall_s") or M.default_wall_s(task.get("difficulty"))
    return task, float(wall)


def _state_path(ledger: str, uid: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(ledger)), "state", uid + ".json")


def cmd_setup(a) -> int:
    task, wall = _unit(a.unit, a.suite)
    R.run_cleanup(task, log=lambda *_: None)
    if not R.run_setup(task, log=lambda *_: None):
        print("SETUP FAILED")
        return 2
    os.makedirs(os.path.dirname(_state_path(a.ledger, a.unit)), exist_ok=True)
    with open(_state_path(a.ledger, a.unit), "w") as f:
        json.dump({"unit": a.unit, "t_setup": time.time(), "wall_budget_s": wall}, f)
    print(f"UNIT: {a.unit}")
    print(f"WALL BUDGET: {wall:.0f} s (a measured model is stopped at this)")
    print("TASK:")
    print(task["task"])
    return 0


def cmd_grade(a) -> int:
    task, wall = _unit(a.unit, a.suite)
    st_path = _state_path(a.ledger, a.unit)
    st = json.load(open(st_path)) if os.path.isfile(st_path) else {}
    t_end = time.time()
    with R.fixture_lock(task, log=print):
        R.toolchain_pins()
        R.run_pre_validate(task, log=lambda *_: None)
        passed, out = R.run_validation(task, timeout_scale=a.scale)
        dest = task.get("dest")
        stash = os.path.join(os.path.dirname(os.path.abspath(a.ledger)), "solutions", a.unit)
        if dest and os.path.exists(dest):
            os.makedirs(stash, exist_ok=True)
            if os.path.isdir(dest):
                shutil.copytree(dest, os.path.join(stash, os.path.basename(dest)),
                                symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(dest, stash)
        R.run_cleanup(task, log=lambda *_: None)
    elapsed = t_end - st["t_setup"] if st.get("t_setup") else None
    row = {"unit": a.unit, "passed": bool(passed),
           "indeterminate": bool(R.validator_timed_out(out or "")),
           "validation_output": (out or "").strip()[-2000:],
           "elapsed_s": round(elapsed, 1) if elapsed else None,
           "wall_budget_s": wall,
           "over_budget": bool(elapsed and elapsed > wall),
           "scale": a.scale, "model": a.model, "graded_at": t_end}
    with open(a.ledger, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps({k: row[k] for k in ("unit", "passed", "indeterminate",
                                           "elapsed_s", "over_budget")}))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=("setup", "grade"))
    ap.add_argument("unit")
    ap.add_argument("--suite", default="v5")
    ap.add_argument("--ledger", required=True, help="JSONL the grade rows go to")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--model", default="claude-opus-5-5")
    a = ap.parse_args(argv)
    return cmd_setup(a) if a.action == "setup" else cmd_grade(a)


if __name__ == "__main__":
    sys.exit(main())
