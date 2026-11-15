#!/usr/bin/env python3
"""Model-free prover for 176_intermittent_500s (directory-overlay solutions).

Drives the harness's own lifecycle (run_eval.run_cleanup -> run_setup ->
[overlay] -> run_pre_validate -> run_validation -> run_cleanup), so the 30 s
x scale validator timeout, rlimits and reaping are what a live unit sees.
Arms come from PROVE.json next to this file:

  ref x repeat          must pass every time (flake check)
  pass[]                alternative correct fixes, must pass
  wrong[]               must fail, and with the asserted failure line
  empty                 the fixture as shipped, must fail as asserted
  leak                  harness-gaming probes, must fail as asserted
                        ([name, regex] or [name, regex, "ref"|"empty"]:
                        the layer it goes on, ref by default)
  slow                  slow or hanging fixes, must fail as asserted within
                        budget (one [name, regex] or a list of them)
  ref @ scale 2.0       must pass (--jobs contention)

Every arm must also finish within budget_s x scale.

usage: prove.py [--repo REPO]      (run with the harness python, >= 3.10)
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
ap.add_argument("--repeat", type=int, default=None)
a = ap.parse_args()
P = json.load(open(os.path.join(HERE, "PROVE.json")))

sys.path.insert(0, os.path.join(a.repo, "evals"))
sys.path.insert(0, os.path.join(a.repo, "agents"))
import run_eval as R  # noqa: E402

(task,) = [t for t in R.load_tasks([P["unit"]]) if t["id"] == P["unit"]]
DEST = P["dest_dir"]
quiet = lambda *_: None  # noqa: E731


def overlay(name):
    src = os.path.join(HERE, name)
    assert os.path.isdir(src), src
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if fn.endswith(".pyc"):
                continue
            s = os.path.join(root, fn)
            d = os.path.join(DEST, os.path.relpath(s, src))
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copyfile(s, d)


def arm(layers, scale):
    R.run_cleanup(task, log=quiet)
    if not R.run_setup(task, log=print):
        return None, 0.0, "SETUP FAILED"
    for name in layers:
        overlay(name)
    R.run_pre_validate(task, log=quiet)
    t0 = time.time()
    ok, out = R.run_validation(task, timeout_scale=scale)
    dt = time.time() - t0
    R.run_cleanup(task, log=quiet)
    return ok, dt, out.strip()


ref = P["ref"]
plan = [("ref#%d" % (i + 1), [ref], True, None, 1.0) for i in range(a.repeat or P["repeat"])]
plan += [("pass:" + n, [n], True, rx, 1.0) for n, rx in P.get("pass", [])]
plan += [("wrong:" + n, [ref, n], False, rx, 1.0) for n, rx in P["wrong"]]
plan.append(("empty(as shipped)", [], False, P["empty"], 1.0))
def arms(v):
    return [v] if isinstance(v[0], str) else v


for lk in arms(P["leak"]):
    plan.append(("leak:" + lk[0], ([] if lk[2:] == ["empty"] else [ref]) + [lk[0]], False, lk[1], 1.0))
for sl in arms(P["slow"]):
    plan.append(("slow:" + sl[0], [ref, sl[0]], False, sl[1], 1.0))
plan.append(("ref@scale2.0", [ref], True, None, 2.0))

bad = 0
for label, layers, want, rx, scale in plan:
    ok, dt, out = arm(layers, scale)
    last = out.splitlines()[-1] if out else ""
    good, why = ok is want, ""
    if good and rx and not re.search(rx, last):
        good, why = False, " [wrong failure class; wanted /%s/]" % rx
    if good and R.validator_timed_out(out):
        good, why = False, " [validator_timeout]"
    if good and dt > P["budget_s"] * scale:
        good, why = False, " [over the %.0fs budget]" % (P["budget_s"] * scale)
    bad += not good
    print("%s %-34s passed=%-5s %5.2fs  %s%s" % ("OK " if good else "BAD", label, ok, dt, last[:110], why))
print("%s: %s" % (P["unit"], "PROVEN" if not bad else "NOT PROVEN (%d bad arm(s))" % bad))
sys.exit(1 if bad else 0)
