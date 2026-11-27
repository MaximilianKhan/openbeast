#!/usr/bin/env python3
"""Model-free proof of 179_git_bisect_revert, driven by PROVE.json.

Exec mode: a "solution" here is not a file dropped at a path but a sequence
of git operations, so each arm runs a shell script with cwd = the fixture
repository (exactly where an agent works), between the harness's OWN
run_cleanup -> run_setup and run_pre_validate -> run_validation ->
run_cleanup. Timeouts (30 s x scale), rlimits and reaping are what a live
unit sees.

Arms: ref x repeat (must pass, slowest validation <= 15 s at scale 1);
every wrong solution (must fail AND the validator's last line must match its
asserted failure class); empty (must fail, class asserted); fixture leak scan
(setup creates exactly fixture_top / fixture_refs, no oracle-like file
anywhere under /tmp/eval_bisect); leak probe (must fail, class asserted);
slow (must fail by the validator's own limit, never the harness
validator_timeout, class asserted); ref again at --scale 2.0.

usage: prove.py [--repo R] [--scale2 2.0] [--repeat N]
"""
import argparse, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
ap.add_argument("--scale2", type=float, default=2.0)
ap.add_argument("--repeat", type=int, default=None)
a = ap.parse_args()

sys.path.insert(0, os.path.join(a.repo, "evals"))
sys.path.insert(0, os.path.join(a.repo, "agents"))
import run_eval as R  # noqa: E402

spec = json.load(open(os.path.join(HERE, "PROVE.json")))
unit = spec["task"]
(task,) = [t for t in R.load_tasks([unit]) if t["id"] == unit]

# Hold the unit's /tmp/eval_* roots for the whole proof: a sweep or another
# prover on this host must not rm -rf them under our arms, nor we under its
# units (run_eval.fixture_lock; the kernel drops it when this process exits).
try:
    _FIXTURE_LOCK = R.fixture_lock(task, wait=False).__enter__()
except R.FixtureBusy as _e:
    sys.exit("%s: NOT PROVEN (fixture busy: %s)" % (task["id"], _e))
dest = spec["dest"]
top = os.path.dirname(dest)
quiet = lambda *_: None  # noqa: E731
LEAK_NAME = re.compile(r"^(expected|answer|check|oracle|hidden|solution|culprit|bad)", re.I)


def fixture_problems():
    probs = []
    if sorted(os.listdir(top)) != sorted(spec["fixture_top"]):
        probs.append("unexpected entries in %s: %s" % (top, sorted(os.listdir(top))))
    refs = subprocess.run(["git", "-C", dest, "for-each-ref", "--format=%(refname)"],
                          stdout=subprocess.PIPE, universal_newlines=True).stdout.split()
    if sorted(refs) != sorted(spec["fixture_refs"]):
        probs.append("unexpected refs: %s" % refs)
    for root, dirs, files in os.walk(top):
        if os.path.join(dest, ".git") in root:
            continue
        probs += [os.path.join(root, f) for f in files if LEAK_NAME.match(f)]
    return probs


def arm(sol, scale=1.0):
    R.run_cleanup(task, log=quiet)
    if not R.run_setup(task, log=print):
        return None, 0.0, "SETUP FAILED", ["setup failed"]
    probs = fixture_problems()
    if sol:
        subprocess.run(["bash", os.path.join(HERE, sol)], cwd=dest, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    R.run_pre_validate(task, log=quiet)
    t0 = time.time()
    ok, out = R.run_validation(task, timeout_scale=scale)
    dt = time.time() - t0
    R.run_cleanup(task, log=quiet)
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    return ok, dt, tail, probs


rows = []  # (good, label, ok, dt, detail)
n = a.repeat or spec.get("repeat", 3)
for i in range(n):
    ok, dt, tail, probs = arm(spec["ref"])
    rows.append((ok is True and dt <= 15.0, "ref#%d" % (i + 1), ok, dt, tail[:80]))
    if probs:
        rows.append((False, "leak:fixture", None, 0.0, "; ".join(probs)[:120]))
for sol, rx in spec["wrong"]:
    ok, dt, tail, _ = arm(sol)
    good = ok is False and not R.validator_timed_out(tail) and re.search(rx, tail) is not None
    rows.append((good, "wrong:" + sol, ok, dt, tail[:80] if good else "WANT /%s/ GOT %s" % (rx, tail[:70])))
ok, dt, tail, _ = arm(None)
rows.append((ok is False and re.search(spec["empty_expect"], tail) is not None, "empty", ok, dt, tail[:80]))
sol, rx = spec["leak"]
ok, dt, tail, _ = arm(sol)
rows.append((ok is False and re.search(rx, tail) is not None, "leak-probe", ok, dt, tail[:80]))
sol, rx = spec["slow"]
ok, dt, tail, _ = arm(sol)
rows.append((ok is False and not R.validator_timed_out(tail) and dt < R.VALIDATION_TIMEOUT_S
             and re.search(rx, tail) is not None, "slow", ok, dt, tail[:80]))
ok, dt, tail, _ = arm(spec["ref"], scale=a.scale2)
rows.append((ok is True, "ref@scale%.1f" % a.scale2, ok, dt, tail[:80]))
for k in ("setup", "cleanup"):
    if re.search(r"(?:^|[\s;&|(`])timeout\s", " " + str(task.get(k, ""))):
        rows.append((False, "lint", None, 0.0, k + " uses coreutils timeout"))

bad = 0
for good, label, ok, dt, detail in rows:
    bad += not good
    print("%s %-28s passed=%-5s %5.2fs  %s" % ("OK " if good else "BAD", label, ok, dt, detail))
print("%s: %s" % (unit, "PROVEN" if not bad else "NOT PROVEN (%d bad arm(s))" % bad))
sys.exit(1 if bad else 0)
