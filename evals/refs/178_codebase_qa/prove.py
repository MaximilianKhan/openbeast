#!/usr/bin/env python3
"""Model-free proof of 178_codebase_qa, driven by PROVE.json.

Uses the harness's own lifecycle (run_eval.run_cleanup -> run_setup ->
[answers drop] -> run_pre_validate -> run_validation -> run_cleanup), so the
30 s x scale validator timeout, rlimits and reaping are what a live unit sees.

Arms: ref x repeat and every also_pass file (must pass); every wrong file
(must fail AND the validator's last line must match the asserted failure
class); empty (must fail, class asserted); leak probe (must fail, class
asserted); ref again at --scale 2.0. Extra gates: setup creates exactly
the bundled repo (no oracle files), the task JSON holds no plaintext
string answer, and solve.py re-run on the extracted fixture reproduces
answers.json.

usage: prove.py [--repo R] [--scale2 2.0]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
ap.add_argument("--scale2", type=float, default=2.0)
a = ap.parse_args()

sys.path.insert(0, os.path.join(a.repo, "evals"))
sys.path.insert(0, os.path.join(a.repo, "agents"))
import run_eval as R  # noqa: E402

spec = json.load(open(os.path.join(HERE, "PROVE.json")))
unit = spec["unit"]
(task,) = [t for t in R.load_tasks([unit]) if t["id"] == unit]
dest = task["dest"]
D = spec["dest_dir"]
quiet = lambda *_: None  # noqa: E731
bad = 0


def expected_files():
    out = []
    root = os.path.join(HERE, "repo")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in filenames:
            if not f.endswith(".pyc") and f != ".DS_Store":
                out.append(os.path.join("repo", os.path.relpath(os.path.join(dirpath, f), root)))
    return sorted(out)


def fixture_files():
    out = []
    for dirpath, _, filenames in os.walk(D):
        for f in filenames:
            out.append(os.path.relpath(os.path.join(dirpath, f), D))
    return sorted(out)


def arm(sol=None, script=None, scale=1.0, inspect=None):
    R.run_cleanup(task, log=quiet)
    if not R.run_setup(task, log=print):
        return None, 0.0, "SETUP FAILED"
    if inspect:
        inspect()
    if sol:
        shutil.copy(os.path.join(HERE, sol), dest)
    if script:
        subprocess.run([sys.executable, "-I", os.path.join(HERE, script), D], check=True,
                       capture_output=True)
    R.run_pre_validate(task, log=quiet)
    t0 = time.time()
    ok, out = R.run_validation(task, timeout_scale=scale)
    dt = time.time() - t0
    R.run_cleanup(task, log=quiet)
    return ok, dt, out.strip().splitlines()[-1] if out.strip() else ""


# -- static gates ---------------------------------------------------------
answers = json.load(open(os.path.join(HERE, spec["ref"])))
visible = task["task"] + task["validation"]["script"] + task["cleanup"]
plain = [v for v in answers.values() if isinstance(v, str)] + \
        [x for v in answers.values() if isinstance(v, list) for x in v]
# Short role names ("admin", "lead") are ordinary words in the prompt.
leaked = [s for s in plain if len(s) >= 8 and s in visible]
print(f"{'OK ' if not leaked else 'BAD'} {unit} {'no-plaintext-answers':26s} {leaked or ''}")
bad += bool(leaked)

state = {}


def check_fixture():
    state["files"] = fixture_files()
    got = subprocess.run([sys.executable, "-B", os.path.join(HERE, "solve.py"), os.path.join(D, "repo")],
                         capture_output=True, text=True, env={"PATH": os.environ.get("PATH", "")})
    state["rederived"] = json.loads(got.stdout) if got.returncode == 0 else got.stderr[-300:]


rows = []  # (label, want_pass, ok, dt, tail, regex)
for i in range(spec.get("repeat", 3)):
    rows.append((f"ref#{i+1}", True, *arm(spec["ref"], inspect=check_fixture if i == 0 else None), None))
for f in spec.get("also_pass", []):
    rows.append((f"pass:{os.path.basename(f)}", True, *arm(f), None))
for f, rx in spec["wrong"]:
    rows.append((f"wrong:{os.path.basename(f)}", False, *arm(f), rx))
rows.append(("empty", False, *arm(None), spec["empty"]))
rows.append((f"leak:{spec['leak'][0]}", False, *arm(script=spec["leak"][0]), spec["leak"][1]))
rows.append((f"ref@scale{a.scale2}", True, *arm(spec["ref"], scale=a.scale2), None))

fx_ok = state.get("files") == expected_files()
print(f"{'OK ' if fx_ok else 'BAD'} {unit} {'fixture-files':26s} {len(state.get('files', []))} files"
      + ("" if fx_ok else f" != {len(expected_files())} expected"))
bad += not fx_ok
rd_ok = state.get("rederived") == answers
print(f"{'OK ' if rd_ok else 'BAD'} {unit} {'rederive-from-fixture':26s} "
      + ("solve.py on the extracted repo == answers.json" if rd_ok else str(state.get("rederived"))[:200]))
bad += not rd_ok

for label, want, ok, dt, tail, rx in rows:
    good = ok is want
    if good and rx and not re.search(rx, tail):
        good = False
        tail += f"   <-- expected /{rx}/"
    if good and not want and R.VALIDATION_TIMED_OUT in tail:
        good = False  # must fail on its own, inside the budget
    if good and dt > 15.0 and "scale" not in label:
        good = False
        tail += "   <-- over the 15 s validator budget"
    bad += not good
    print(f"{'OK ' if good else 'BAD'} {unit} {label:26s} passed={ok!s:5s} {dt:5.2f}s  {tail[:150]}")
print(f"{unit}: {'PROVEN' if not bad else f'NOT PROVEN ({bad} bad arm(s))'}")
sys.exit(1 if bad else 0)
