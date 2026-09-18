#!/usr/bin/env python3
"""Replace one task row in an eval results file with a corrected rerun.

Unlike merge_minis.py (which only ADDS rows that are absent), this REPLACES an
existing row and records why. Written 2026-09-15 for the unit a review subagent
destroyed with an over-broad pkill while the row was live: the runner took a
SIGTERM mid-write, so the harness banked an honest-looking FAIL whose validation
output is just "go.mod not found". Leaving it in would understate the artifact.

usage: patchup_replace.py <main.json> <rerun.json> <task_id> <reason>
Writes main.json in place (after a .bak), and refuses if the rerun does not
contain the task or if the row it is replacing was not the one described.
"""
import json, shutil, sys, datetime

main_p, rerun_p, task_id, reason = sys.argv[1:5]
main = json.load(open(main_p))
rerun = json.load(open(rerun_p))

new = next((t for t in rerun.get("tasks", []) if t["id"] == task_id), None)
if new is None:
    sys.exit(f"patchup: rerun {rerun_p} has no task {task_id} — refusing")
old_i = next((i for i, t in enumerate(main.get("tasks", [])) if t["id"] == task_id), None)
if old_i is None:
    sys.exit(f"patchup: {main_p} has no task {task_id} — refusing")

old = main["tasks"][old_i]
shutil.copy(main_p, main_p + ".bak")
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
json.dump(main, open(main_p, "w"), indent=1)
print(f"patchup: {task_id} {old.get('passed')} -> {new.get('passed')} "
      f"(exit {old.get('agent_exit_code')} -> {new.get('agent_exit_code')}) in {main_p}")
