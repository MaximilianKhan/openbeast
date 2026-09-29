#!/usr/bin/env python3
"""Replace one task row in an eval results file with a corrected rerun.

Unlike merge_minis.py (which only ADDS rows that are absent), this REPLACES an
existing row and records why. Written 2026-09-15 for the unit a review subagent
destroyed with an over-broad pkill while the row was live: the runner took a
SIGTERM mid-write, so the harness banked an honest-looking FAIL whose validation
output is just "go.mod not found". Leaving it in would understate the artifact.

The header is re-derived from the rows after every replacement (2026-09-29
review, research-stats-4): summary.passed/failed, and — when the file carries
a fast_suite block — the imputed capability and the tripwire list, computed the
same way run_eval does. The first version swapped the row and left the header
alone, so the 09-14 UD-IQ3 file said 73 passed / 97.42 with a "failed"
tripwire that its own rows showed passing (76 / 97.53).

usage: patchup_replace.py <main.json> <rerun.json> <task_id> <reason>
       patchup_replace.py --recompute <main.json>   (header only, no row swap)
Writes main.json in place (after a .bak — .bak.1, .bak.2... once one exists, so
the original row survives a second patch-up), and refuses if the rerun does not
contain the task or if the row it is replacing was not the one described.
"""
import datetime
import json
import os
import shutil
import sys

EVALS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals")


def backup(path):
    """Copy path to the first free path.bak, path.bak.1, ... — never over an
    older backup, which is the only surviving copy of the row it replaced."""
    dst, n = path + ".bak", 0
    while os.path.exists(dst):
        n += 1
        dst = f"{path}.bak.{n}"
    shutil.copy(path, dst)
    return dst


def recompute_header(main):
    """Re-derive summary (and fast_suite, if present) from main['tasks']."""
    tasks = main.get("tasks", [])
    passed = sum(1 for t in tasks if t.get("passed"))
    main["summary"] = {**main.get("summary", {}), "total": len(tasks),
                       "passed": passed, "failed": len(tasks) - passed}
    fs = main.get("fast_suite")
    if fs and fs.get("suite"):
        sys.path.insert(0, EVALS)
        import scoring  # noqa: E402 — the harness's own imputation, not a copy
        pin = json.load(open(os.path.join(EVALS, "suites", f"{fs['suite']}.json")))
        trip = set(pin["tripwires"])
        imputed = scoring.impute_suite_tasks(tasks, pin)
        solve, lang, cap = scoring.compute_solve_breadth(imputed)
        main["fast_suite"] = {**fs,
                              "capability_imputed": cap,
                              "problem_solving_imputed": solve,
                              "language_breadth_imputed": lang,
                              "imputed_units": len(imputed) - len(tasks),
                              "tripwire_failures": [t["id"] for t in tasks
                                                    if not t.get("passed") and t["id"] in trip]}
    return main


def replace(main, rerun, task_id, reason, rerun_p):
    new = next((t for t in rerun.get("tasks", []) if t["id"] == task_id), None)
    if new is None:
        sys.exit(f"patchup: rerun {rerun_p} has no task {task_id} — refusing")
    old_i = next((i for i, t in enumerate(main.get("tasks", [])) if t["id"] == task_id), None)
    if old_i is None:
        sys.exit(f"patchup: main has no task {task_id} — refusing")
    old = main["tasks"][old_i]
    main["tasks"][old_i] = new
    prov = main.setdefault("provenance", {}).setdefault("patchup_replaced", [])
    prov.append({
        "task": task_id,
        "reason": reason,
        "replaced_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "from": rerun_p,
        "old": {k: old.get(k) for k in
                ("passed", "agent_exit_code", "elapsed_seconds", "validation_output")},
        "new": {k: new.get(k) for k in
                ("passed", "agent_exit_code", "elapsed_seconds", "validation_output")},
    })
    return old, new


def main(argv):
    if argv[:1] == ["--recompute"] and len(argv) == 2:
        main_p = argv[1]
        main = json.load(open(main_p))
        before = (dict(main.get("summary", {})), dict(main.get("fast_suite") or {}))
        backup(main_p)
        recompute_header(main)
        json.dump(main, open(main_p, "w"), indent=1)
        print(f"patchup: header recomputed in {main_p}: summary {before[0]} -> {main['summary']}"
              + (f"; capability {before[1].get('capability_imputed')} -> "
                 f"{main['fast_suite']['capability_imputed']}, tripwires "
                 f"{before[1].get('tripwire_failures')} -> {main['fast_suite']['tripwire_failures']}"
                 if before[1] else ""))
        return 0
    if len(argv) != 4:
        sys.exit(__doc__)
    main_p, rerun_p, task_id, reason = argv
    main = json.load(open(main_p))
    rerun = json.load(open(rerun_p))
    old, new = replace(main, rerun, task_id, reason, rerun_p)
    recompute_header(main)
    backup(main_p)
    json.dump(main, open(main_p, "w"), indent=1)
    print(f"patchup: {task_id} {old.get('passed')} -> {new.get('passed')} "
          f"(exit {old.get('agent_exit_code')} -> {new.get('agent_exit_code')}) in {main_p}; "
          f"summary now {main['summary']['passed']}/{main['summary']['total']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
