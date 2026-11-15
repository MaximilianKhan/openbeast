#!/usr/bin/env python3
"""Model-free proof of 196_polyphase_resampler, driven by PROVE.json.

Runs the harness's own lifecycle (run_eval.run_cleanup -> run_setup ->
solution drop -> run_pre_validate -> run_validation -> run_cleanup) for every
arm, and asserts the failure CLASS of each failing arm by regex on the
validator's last line:

  ref x5 (timed task)   must pass
  wrong (each)          must fail with its regex
  empty                 must fail with its regex
  leak probe            must fail with its regex
  slow                  must fail with its regex, inside the harness timeout
  ref at --scale 2.0    must pass

usage: python3 evals/refs/196_polyphase_resampler/prove.py   (from anywhere)
"""
import json, os, re, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "evals"))
sys.path.insert(0, os.path.join(REPO, "agents"))
import run_eval as R  # noqa: E402

P = json.load(open(os.path.join(HERE, "PROVE.json")))
quiet = lambda *_: None


def arm(task, sol, scale=1.0):
    R.run_cleanup(task, log=quiet)
    if not R.run_setup(task, log=print):
        return None, 0.0, "SETUP FAILED"
    if sol:
        os.makedirs(os.path.dirname(P["dest"]), exist_ok=True)
        shutil.copy(os.path.join(HERE, sol), P["dest"])
    R.run_pre_validate(task, log=quiet)
    t0 = time.time()
    ok, out = R.run_validation(task, timeout_scale=scale)
    dt = time.time() - t0
    R.run_cleanup(task, log=quiet)
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    return ok, dt, tail


bad_total = 0
for unit, u in P["units"].items():
    (task,) = [t for t in R.load_tasks([unit]) if t["id"] == unit]
    plan = [("ref#%d" % (i + 1), u["ref"], None, 1.0)
            for i in range(5 if P.get("timed") else 3)]
    plan += [("wrong:" + w, w, rx, 1.0) for w, rx in u["wrong"]]
    plan.append(("empty", None, u["empty"], 1.0))
    plan.append(("leak:" + u["leak"][0], u["leak"][0], u["leak"][1], 1.0))
    if "slow" in u:
        plan.append(("slow:" + u["slow"][0], u["slow"][0], u["slow"][1], 1.0))
    plan.append(("ref@scale2.0", u["ref"], None, 2.0))
    bad = 0
    for label, sol, rx, scale in plan:
        ok, dt, tail = arm(task, sol, scale)
        if rx is None:
            good = ok is True
        else:
            good = (ok is False and re.search(rx, tail) is not None
                    and not R.validator_timed_out(tail))
        bad += not good
        print("%s %-26s passed=%-5s %6.2fs  %s"
              % ("OK " if good else "BAD", label, ok, dt, tail[:90]))
    print("%s: %s" % (unit, "PROVEN" if not bad else "NOT PROVEN (%d bad arm(s))" % bad))
    bad_total += bad
sys.exit(1 if bad_total else 0)
